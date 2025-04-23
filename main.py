import asyncio
import websockets
import json
import logging
import platform
import subprocess
import inspect
import os # To read environment variables
import collections # For deque (history)
import shlex # For safely splitting command for Popen when shell=False
import sys # To check platform

import subprocess
import screen_brightness_control as sbc

# Attempt to import distro, optional dependency
try:
    import distro
    distro_info = f"{distro.name()} {distro.version()}"
except ImportError:
    distro_info = None
    logging.warning("'distro' library not found (pip install distro). OS detection might be less accurate.")

# --- Potentially Required Libraries (Install if needed) ---
# pip install websockets psutil screen-brightness-control openai pycaw pulsectl-asyncio python-dotenv distro
# Load environment variables (e.g., for OPENAI_API_KEY)
from dotenv import load_dotenv
load_dotenv()

# --- Security Configuration ---
ALLOW_SHELL_EXECUTION = os.getenv('ALLOW_SHELL_EXECUTION', 'false').lower() == 'true'
SHELL_COMMAND_TIMEOUT = 30 # Timeout for shell commands in seconds
MAX_ERROR_FEEDBACK_ATTEMPTS = 1 # Number of times to feed back error for correction

# --- Platform Info ---
os_name = platform.system()
is_linux = (os_name == "Linux")

# --- Distro Info (Linux Only) ---
if is_linux and not distro_info:
    # Fallback using platform module if distro package failed
    try:
        # platform.uname() gives basic info
        plat_uname = platform.uname()
        distro_info = f"{plat_uname.system} {plat_uname.release}" # Less specific than 'distro'
    except Exception:
        distro_info = "Linux (Unknown Distribution)" # Generic fallback
elif not is_linux:
    distro_info = f"{os_name} {platform.release()}" # Use platform info for non-Linux

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info(f"Detected OS / Distro: {distro_info}")
if ALLOW_SHELL_EXECUTION:
    logging.warning("="*60)
    logging.warning("🛑 SECURITY WARNING: Arbitrary shell command execution is ENABLED.")
    logging.warning("   Set ALLOW_SHELL_EXECUTION=false environment variable to disable.")
    logging.warning("   Ensure the server is not exposed to untrusted networks/users.")
    logging.warning("="*60)
else:
    logging.info("Shell command execution is disabled (ALLOW_SHELL_EXECUTION is not 'true').")


# --- Library Imports & Checks ---
try: import psutil
except ImportError: logging.warning("psutil library not found."); psutil = None
try: import screen_brightness_control as sbc
except ImportError: logging.warning("screen-brightness-control library not found."); sbc = None

# --- Platform Specific Volume Control Libraries ---
pycaw = None; pulsectl = None
# ... (volume library loading as before) ...
if os_name == "Windows":
    try: from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume; from comtypes import CLSCTX_ALL; pycaw = True
    except ImportError: logging.warning("pycaw library not found.")
elif is_linux:
     try: import pulsectl_asyncio; pulsectl = True
     except ImportError: logging.warning("pulsectl-asyncio library not found.")

# --- OpenAI Client Setup ---
try:
    from openai import AsyncOpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: logging.error("OPENAI_API_KEY environment variable not set."); openai_available = False; client = None
    else: client = AsyncOpenAI(api_key=api_key); openai_available = True
except ImportError: logging.warning("openai library not found."); openai_available = False; client = None


# --- Server Configuration ---
HOST = 'localhost'
PORT = 8765
HISTORY_MAX_LEN = 8 # Increased slightly for potential error messages

# --- OpenAI API Call Function (Unchanged) ---
async def call_openai_api(command, history=None):
    """
    Calls the OpenAI API to interpret the command, considering conversation history and OS info.
    """
    if not openai_available or not client:
        return {"intent": "unknown", "parameters": {"error": "OpenAI interpretation is unavailable."}}

    logging.info(f"Querying LLM for: '{command}' with history and OS info.")

    # --- Updated System Prompt ---
    run_shell_command_prompt = f"""
- "run_shell_command": requires "command" (string, the exact shell command to execute).
    - Example (listing files): {{"intent": "run_shell_command", "parameters": {{"command": "ls -l /tmp"}}}}
    - Example (opening app): {{"intent": "run_shell_command", "parameters": {{"command": "firefox &"}}}}
    - Example (opening file): {{"intent": "run_shell_command", "parameters": {{"command": "xdg-open mydocument.pdf &"}}}}
    - IMPORTANT: For opening GUI applications or files on Linux, ALWAYS append ' &' to the command string to run it in the background.""" if ALLOW_SHELL_EXECUTION and is_linux else ""
    security_note = f"\n\nIMPORTANT SECURITY NOTE: The 'run_shell_command' intent is ENABLED via server configuration and allows executing arbitrary commands. Be extremely careful what you ask it to run." if ALLOW_SHELL_EXECUTION else "\n\nNOTE: The 'run_shell_command' intent is currently disabled by server configuration for security."

    system_prompt = f"""
You are a helpful assistant interpreting user commands for controlling a device or executing tasks on the target system.
The target system is running: {distro_info}. Use this information to generate appropriate commands (e.g., package managers like apt/dnf/pacman if applicable).
Identify the user's intent and extract relevant parameters based ONLY on the provided command AND PREVIOUS CONTEXT if relevant (e.g., pronouns like "it", or sequential commands based on previous output).
If the previous command execution resulted in an error (indicated by a SYSTEM_NOTE in the history), analyze the error and try to generate a corrected command if the user's request implies fixing it.
Respond ONLY with a valid JSON object containing 'intent' and 'parameters' keys. Do not add any explanation or surrounding text.

Available intents and their parameters:
- "set_brightness": requires "level" (integer 0-100).
- "toggle_wifi": requires "state" ("on" or "off").
- "toggle_bluetooth": requires "state" ("on" or "off").
- "set_volume": requires "level" (integer 0-100).
- "get_volume": no parameters required.
- "get_battery_status": no parameters required.
- "get_cpu_usage": no parameters required.
- "get_memory_usage": no parameters required.
{run_shell_command_prompt}

If the command is unclear, doesn't match an available intent, or lacks required parameters even considering context and error history,
respond with: {{"intent": "unknown", "parameters": {{"error": "Command not understood or parameters missing."}}}}
{security_note}
"""
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for role, content in history:
            messages.append({"role": role, "content": str(content)})
    messages.append({"role": "user", "content": command})

    try:
        response = await client.chat.completions.create(
            model="o4-mini",
            # model="gpt-4o",
            messages=messages,
            response_format={ "type": "json_object" }
        )
        content = response.choices[0].message.content
        interpretation = json.loads(content)

        if not ALLOW_SHELL_EXECUTION and interpretation.get("intent") == "run_shell_command":
             logging.warning("LLM attempted to run shell command while disabled. Blocking.")
             return {"intent": "unknown", "parameters": {"error": "Shell command execution is disabled by server configuration."}}

        if isinstance(interpretation, dict) and "intent" in interpretation and "parameters" in interpretation:
             logging.info(f"OpenAI interpretation successful: {interpretation}")
             return interpretation
        else:
             logging.error(f"OpenAI response is not valid JSON or lacks required keys: {content}")
             return {"intent": "unknown", "parameters": {"error": "Failed to interpret command via AI (invalid format)."}}
    except Exception as e:
        logging.exception(f"An unexpected error occurred during OpenAI API call: {e}")
        return {"intent": "unknown", "parameters": {"error": f"Unexpected AI error: {e}"}}


# --- Combined Command Interpretation (Unchanged) ---
async def interpret_command_with_llm(command, history=None):
    """
    Interprets the command using hardcoded rules first, then falls back to OpenAI API (passing history).
    """
    logging.info(f"Interpreting command: '{command}'")
    command_lower = command.lower()
    cl = command.lower()
    interpretation = None
    # --- 1. Hardcoded Rules ---
    if command_lower == "get cpu usage": interpretation = {"intent": "get_cpu_usage", "parameters": {}}
    elif command_lower == "get memory usage": interpretation = {"intent": "get_memory_usage", "parameters": {}}
    elif command_lower == "get_volume": interpretation = {"intent": "get_volume", "parameters": {}}
    elif command_lower == "get_battery_status": interpretation = {"intent": "get_battery_status", "parameters": {}}
    elif "wifi" in cl and "status" in cl:
        interpretation = {"intent": "get_wifi_status", "parameters": {}}
    elif "bluetooth" in cl and "status" in cl:
        interpretation = {"intent": "get_bluetooth_status", "parameters": {}}
    elif "brightness" in command_lower: # ... (brightness logic)
        level = None; words = command_lower.split(); # ... find level ...
        if level is not None: interpretation = {"intent": "set_brightness", "parameters": {"level": max(0, min(100, level))}}
    elif "volume" in command_lower or "sound" in command_lower or "mute" in command_lower: # ... (volume logic)
        level = None; # ... find level or mute ...
        if level is not None: interpretation = {"intent": "set_volume", "parameters": {"level": max(0, min(100, level))}}
    elif "wi-fi" in command_lower or "wifi" in command_lower: # ... (wifi logic)
        if "on" in command_lower or "enable" in command_lower: interpretation = {"intent": "toggle_wifi", "parameters": {"state": "on"}}
        elif "off" in command_lower or "disable" in command_lower: interpretation = {"intent": "toggle_wifi", "parameters": {"state": "off"}}
    elif "bluetooth" in command_lower: # ... (bluetooth logic)
        if "on" in command_lower or "enable" in command_lower: interpretation = {"intent": "toggle_bluetooth", "parameters": {"state": "on"}}
        elif "off" in command_lower or "disable" in command_lower: interpretation = {"intent": "toggle_bluetooth", "parameters": {"state": "off"}}
    elif "battery" in command_lower or "charging" in command_lower or "power level" in command_lower: interpretation = {"intent": "get_battery_status", "parameters": {}}
    elif "cpu" in command_lower and "usage" in command_lower: interpretation = {"intent": "get_cpu_usage", "parameters": {}}
    elif ("memory" in command_lower or "ram" in command_lower) and "usage" in command_lower: interpretation = {"intent": "get_memory_usage", "parameters": {}}

    # --- 2. Fallback to OpenAI API ---
    if interpretation:
        logging.info(f"Command matched by hardcoded rule: {interpretation}")
        return interpretation
    else:
        return await call_openai_api(command, history=history)

# --- WebSocket Handler (Unchanged) ---
async def handler(websocket):
    logging.info(f"Client connected from {websocket.remote_address}")
    history = collections.deque(maxlen=HISTORY_MAX_LEN)
    last_shell_error_info = None
    error_feedback_count = 0
    try:
        async for message in websocket:
            current_history = list(history)
            if last_shell_error_info and error_feedback_count < MAX_ERROR_FEEDBACK_ATTEMPTS:
                 error_context = (f"SYSTEM_NOTE: The previous command '{last_shell_error_info['command']}' failed (Exit Code: {last_shell_error_info['exit_code']}, Stderr: {last_shell_error_info['stderr'] or '(none)'}). Please analyze this error and try to correct the command based on the user's *current* request: '{message}'")
                 current_history.append(("assistant", error_context))
                 logging.info(f"Providing error context to LLM (Attempt {error_feedback_count + 1})")
                 error_feedback_count += 1
            else: last_shell_error_info = None; error_feedback_count = 0

            interpretation = await interpret_command_with_llm(message, history=current_history)
            intent = interpretation.get("intent")
            parameters = interpretation.get("parameters", {})
            response_message = ""; structured_response = None

            if intent == "run_shell_command" and not ALLOW_SHELL_EXECUTION:
                 response_message = "Error: Shell command execution is disabled by server configuration."; intent = "error_blocked"

            if intent in INTENT_HANDLERS:
                handler_func = INTENT_HANDLERS[intent]
                try:
                    if inspect.iscoroutinefunction(handler_func): response_message = await handler_func(**parameters)
                    else: response_message = handler_func(**parameters)
                    if intent == "run_shell_command" and isinstance(response_message, dict):
                        structured_response = response_message # Keep the structured response
                        # Check the success flag determined by execute_shell_command
                        if not response_message.get("success"):
                            # Store error info for potential feedback on the *next* message
                            last_shell_error_info = { "command": response_message.get("command"), "exit_code": response_message.get("exit_code"), "stderr": response_message.get("stderr") or response_message.get("error_message") }
                            # Format a user-friendly error message from the structured data
                            response_message = (f"Error executing command: {response_message.get('command', '')}\nExit Code: {response_message.get('exit_code', 'N/A')}\nStderr: {response_message.get('stderr') or response_message.get('error_message') or '(None)'}")
                        else:
                             # Command succeeded according to new criteria, clear error state
                             last_shell_error_info = None; error_feedback_count = 0
                             # Format success message
                             response_message = (f"Command executed successfully: {response_message.get('command', '')}\nExit Code: {response_message.get('exit_code', 0)}\nStdout: {response_message.get('stdout') or '(None)'}")
                except TypeError as e: logging.error(f"Parameter mismatch for intent '{intent}': {e}. Params: {parameters}"); response_message = f"Error: Incorrect parameters provided for action '{intent}'."; last_shell_error_info = None; error_feedback_count = 0
                except Exception as e: logging.exception(f"Error executing handler for intent '{intent}': {e}"); response_message = f"Error executing action for '{intent}': {e}"; last_shell_error_info = None; error_feedback_count = 0
            elif intent == "unknown": response_message = f"Command not understood. {parameters.get('error', '')}"; last_shell_error_info = None; error_feedback_count = 0
            elif intent == "error_blocked": pass; last_shell_error_info = None; error_feedback_count = 0
            else: response_message = f"No handler defined for intent: {intent}"; last_shell_error_info = None; error_feedback_count = 0

            final_response_data_to_send = None; history_entry_assistant = None
            if structured_response: final_response_data_to_send = {"response": structured_response}; history_entry_assistant = json.dumps(structured_response)
            else:
                try: json_data = json.loads(response_message); final_response_data_to_send = {"response": json_data}; history_entry_assistant = json.dumps(json_data)
                except (json.JSONDecodeError, TypeError): final_response_data_to_send = {"response": str(response_message)}; history_entry_assistant = str(response_message)
            await websocket.send(json.dumps(final_response_data_to_send))
            history.append(("user", message)); history.append(("assistant", history_entry_assistant))
    except websockets.exceptions.ConnectionClosedOK: logging.info(f"Client {websocket.remote_address} disconnected normally.")
    except websockets.exceptions.ConnectionClosedError as e: logging.error(f"Client {websocket.remote_address} disconnected with error: {e}")
    except Exception as e: logging.exception(f"An unexpected error occurred with client {websocket.remote_address}: {e}"); 
    try: await websocket.send(json.dumps({"error": f"Server error occurred."})) 
    except: pass
    finally: logging.info(f"Connection closed for {websocket.remote_address}")


# --- Start Server ---
async def main():
    logging.info(f"Starting WebSocket server on ws://{HOST}:{PORT}")
    async with websockets.serve(handler, HOST, PORT, ping_interval=20, ping_timeout=20):
        await asyncio.Future()

import sys
import subprocess
import json
import asyncio
import platform  # Use platform module for better OS detection

# --- Attempt to import libraries ---
try:
    import psutil
except ImportError:
    psutil = None
    print("Warning: psutil library not found. CPU, Memory, and Battery functions will not work.")

try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None
    print("Warning: screen-brightness-control library not found. Brightness function will not work.")

# Windows-specific import for volume
if sys.platform == 'win32':
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
    except ImportError:
        AudioUtilities = None
        print("Warning: pycaw library not found. Windows volume control will not work.")
else:
     AudioUtilities = None # Define as None for other platforms

# --- Helper function for running subprocess commands ---
async def _run_command(command):
    """Asynchronously runs a shell command."""
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        print(f"Error running command '{command}': {stderr.decode().strip()}")
        return None
    return stdout.decode().strip()


# ——— New: status queries —————————————————————————————————————
async def get_wifi_status() -> str:
    # Run the command; _run_command returns a single string (or None)
    if sys.platform.startswith("linux"):
        result = await _run_command("nmcli radio wifi")
        state = result.lower().strip() if result else "unknown"

    elif sys.platform == "darwin":
        # get device identifier
        dev = await _run_command(
            "networksetup -listallhardwareports | awk '/Hardware Port: Wi-Fi/{getline; print $2}'"
        )
        out = await _run_command(f"networksetup -getairportpower {dev}")
        state = "on" if out and "On" in out else "off"

    elif sys.platform == "win32":
        out = await _run_command(
            'netsh interface show interface name="Wi-Fi"'
        )
        state = "on" if out and "Connected" in out else "off"

    else:
        state = "unknown"

    return f"Wi‑Fi Status: {state}"


async def get_bluetooth_status() -> str:
    if sys.platform.startswith("linux"):
        out = await _run_command("rfkill list bluetooth")
        state = "off" if out and "Soft blocked: yes" in out else "on"

    elif sys.platform == "darwin":
        out = await _run_command("blueutil --power")
        # blueutil returns "1" or "0"
        state = "on" if out == "1" else "off"

    else:
        state = "unsupported"

    return f"Bluetooth Status: {state}"


# --- Device Control Functions ---

# def set_brightness(level: int) -> str:
#     """Sets the screen brightness.

#     Args:
#         level: Brightness level percentage (0-100).

#     Returns:
#         Status message or error.
#     """
#     if not sbc:
#         return "Error: screen-brightness-control library missing."
#     if not 0 <= level <= 100:
#         return "Error: Brightness level must be between 0 and 100."
#     try:
#         sbc.set_brightness(level)
#         return f"Brightness set to {level}%"
#     except Exception as e:
#         return f"Error setting brightness: {e}"
def set_brightness(level: int) -> str:
    """Sets the screen brightness."""
    # Validate
    if not 0 <= level <= 100:
        return "Error: Brightness level must be between 0 and 100."
    
    try:
        if sys.platform == 'darwin':  # macOS
            try:
                # Use the brightness Python package instead of CLI or AppleScript
                import brightness_control  # Import at function call time
                brightness_control.set_brightness(level)
                return f"Brightness set to {level}% on macOS using brightness-control package."
            except ImportError:
                # Try direct subprocess approach
                normalized = level / 100.0
                subprocess.run(['brightness', f'{normalized}'], check=True)
                return f"Brightness set to {level}% on macOS via brightness command."
        
        # Existing cross-platform logic for non-macOS
        if sbc:
            sbc.set_brightness(level)
            return f"Brightness set to {level}%"
        else:
            return "Error: screen-brightness-control library missing."
    except Exception as e:
        return f"Error setting brightness: {e}"
async def toggle_wifi(state: str) -> str:
    """Toggles Wi-Fi state (on/off). Highly platform-dependent.

    Args:
        state: 'on' or 'off'.

    Returns:
        Status message or error. Requires appropriate permissions.
    """
    command = None
    adapter_name = "Wi-Fi" # Common adapter name, might need adjustment

    if state not in ['on', 'off']:
        return "Error: State must be 'on' or 'off'."

    action = 'enable' if state == 'on' else 'disable'

    try:
        if sys.platform == 'win32':
            # Requires admin privileges
            command = f'netsh interface set interface name="{adapter_name}" admin={action}'
            result = await _run_command(command)
            if result is not None:
                 return f"Wi-Fi toggled {state} (requires admin privileges)."
            else:
                 return f"Error toggling Wi-Fi on Windows. Command: '{command}'. Check adapter name and permissions."

        elif sys.platform == 'darwin': # macOS
            # May require specific permissions or user interaction
            device_id = await _run_command("networksetup -listallhardwareports | awk '/Hardware Port: Wi-Fi/{getline; print $2}'")
            if not device_id:
                 return "Error: Could not determine Wi-Fi device ID on macOS."
            command = f'networksetup -setairportpower {device_id} {state}'
            result = await _run_command(command)
            if result is not None:
                 return f"Wi-Fi toggled {state} on macOS."
            else:
                 return f"Error toggling Wi-Fi on macOS. Command: '{command}'. Check permissions."

        elif sys.platform.startswith('linux'):
            # Requires nmcli (NetworkManager) to be installed and running
            command = f'nmcli radio wifi {state}'
            result = await _run_command(command)
            if result is not None:
                 return f"Wi-Fi toggled {state} via nmcli on Linux."
            else:
                return f"Error toggling Wi-Fi on Linux. Command: '{command}'. Is NetworkManager running?"
        else:
            return f"Error: Wi-Fi toggle not supported on this platform ({sys.platform})."
    except Exception as e:
        return f"An unexpected error occurred toggling Wi-Fi: {e}"


async def toggle_bluetooth(state: str) -> str:
    """Toggles Bluetooth state (on/off). Highly platform-dependent.

    Args:
        state: 'on' or 'off'.

    Returns:
        Status message or error. Requires appropriate permissions/tools.
    """
    # Bluetooth control is even more complex and less standardized than Wi-Fi.
    # Often requires specific tools (e.g., blueutil on macOS, rfkill/bluetoothctl on Linux)
    # or interacting with platform-specific APIs/services.
    # Providing a robust cross-platform implementation here is difficult.
    # Placeholder implementation:
    if state not in ['on', 'off']:
        return "Error: State must be 'on' or 'off'."

    print(f"--- Attempting to toggle Bluetooth {state} ---")
    if sys.platform == 'darwin':
        # Requires 'blueutil' (brew install blueutil)
        try:
            status_code = subprocess.call(['blueutil', '--power', '1' if state == 'on' else '0'])
            if status_code == 0:
                return f"Bluetooth toggled {state} on macOS (using blueutil)."
            else:
                return "Error: Failed to toggle Bluetooth using blueutil. Is it installed?"
        except FileNotFoundError:
             return "Error: 'blueutil' command not found. Please install it (brew install blueutil)."
        except Exception as e:
             return f"Error toggling Bluetooth on macOS: {e}"

    elif sys.platform.startswith('linux'):
         # Requires 'rfkill' or 'bluetoothctl'. Using rfkill is simpler for basic toggle.
         # May require user to be in specific groups (e.g., rfkill, bluetooth).
        try:
            action = 'unblock' if state == 'on' else 'block'
            command = f'rfkill {action} bluetooth'
            result = await _run_command(command)
            # rfkill might not produce stdout on success, check return code implicitly via _run_command
            if result is not None: # Check if command ran without error reported by _run_command
                 # Check status after command
                 status_output = await _run_command("rfkill list bluetooth -n -o SOFT")
                 current_state = "on" if status_output and "unblocked" in status_output else "off"
                 if current_state == state:
                     return f"Bluetooth toggled {state} on Linux (using rfkill)."
                 else:
                     # This case might happen if rfkill is blocked by hardware switch
                     return f"Attempted to toggle Bluetooth {state} using rfkill, but state is still {current_state}. Check hardware switch or permissions."
            else:
                 return f"Error toggling Bluetooth on Linux using rfkill. Command: '{command}'. Check permissions/groups."

        except Exception as e:
            return f"Error toggling Bluetooth on Linux: {e}"

    elif sys.platform == 'win32':
        # Windows Bluetooth control via script is notoriously difficult.
        # Often requires interacting with complex WinRT APIs or third-party libraries.
        # No simple command-line equivalent like netsh for Wi-Fi.
        return "Error: Bluetooth toggle is not reliably supported via script on Windows."
    else:
        return f"Error: Bluetooth toggle not supported on this platform ({sys.platform})."

    # Fallback message if no platform matched or specific logic failed
    # return f"Bluetooth toggle {state} - Platform specific implementation needed."


def get_cpu_usage() -> str:
    """Gets the current CPU usage percentage."""
    if psutil:
        try:
            # interval=0.1 gives a short sample time for a more instant reading
            usage = psutil.cpu_percent(interval=0.1)
            return f"CPU Usage: {usage}%"
        except Exception as e:
            return f"Error getting CPU usage: {e}"
    return "Error: psutil library missing or failed to initialize."

def get_memory_usage() -> str:
    """Gets the current virtual memory usage percentage."""
    if psutil:
        try:
            memory = psutil.virtual_memory()
            return f"Memory Usage: {memory.percent:.1f}%"
        except Exception as e:
            return f"Error getting memory usage: {e}"
    return "Error: psutil library missing or failed to initialize."

# --- Volume Control (Platform Specific) ---

async def set_volume(level: int) -> str:
    """Sets the system master volume.

    Args:
        level: Volume level percentage (0-100).

    Returns:
        Status message or error.
    """
    if not 0 <= level <= 100:
        return "Error: Volume level must be between 0 and 100."

    try:
        if sys.platform == 'win32':
            if not AudioUtilities:
                 return "Error: pycaw library missing for Windows volume control."
            # This part is synchronous, wrap in executor for async context
            def _set_win_volume():
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = interface.QueryInterface(IAudioEndpointVolume)
                # Volume scalar range is 0.0 to 1.0
                volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _set_win_volume) # Runs sync code in thread pool
            return f"Volume set to {level}% on Windows."

        elif sys.platform == 'darwin': # macOS
            command = f"osascript -e 'set volume output volume {level}'"
            result = await _run_command(command)
            if result is not None:
                return f"Volume set to {level}% on macOS."
            else:
                return f"Error setting volume on macOS. Command: '{command}'"

        elif sys.platform.startswith('linux'):
            # Assumes 'amixer' is installed (usually part of alsa-utils)
            # Finds the default 'Master' control. Might need adjustment based on system config.
            command = f"amixer sset Master {level}%"
            # Alternative using pactl (PulseAudio): command = f"pactl set-sink-volume @DEFAULT_SINK@ {level}%"
            result = await _run_command(command)
            if result is not None:
                 return f"Volume set to {level}% on Linux (using amixer)."
            else:
                 return f"Error setting volume on Linux. Command: '{command}'. Is 'amixer' installed and configured?"
        else:
            return f"Error: Volume control not supported on this platform ({sys.platform})."
    except Exception as e:
        return f"An unexpected error occurred setting volume: {e}"


async def get_volume() -> str:
    """Gets the current system master volume."""
    try:
        if sys.platform == 'win32':
            if not AudioUtilities:
                 return "Error: pycaw library missing for Windows volume control."
            # This part is synchronous, wrap in executor for async context
            def _get_win_volume():
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = interface.QueryInterface(IAudioEndpointVolume)
                # Volume scalar range is 0.0 to 1.0
                level = int(volume.GetMasterVolumeLevelScalar() * 100)
                return level
            loop = asyncio.get_running_loop()
            level = await loop.run_in_executor(None, _get_win_volume)
            return f"Current Volume: {level}% (Windows)"

        elif sys.platform == 'darwin': # macOS
            command = "osascript -e 'output volume of (get volume settings)'"
            result = await _run_command(command)
            if result and result.isdigit():
                return f"Current Volume: {result}% (macOS)"
            else:
                # Try getting muted status if volume fetch failed
                muted_command = "osascript -e 'output muted of (get volume settings)'"
                muted_result = await _run_command(muted_command)
                if muted_result == 'true':
                    return "Current Volume: Muted (macOS)"
                return f"Error getting volume on macOS. Command: '{command}', Result: '{result}'"


        elif sys.platform.startswith('linux'):
            # Assumes 'amixer' is installed. Parses output like: "[80%] [-10.00dB] [on]"
            command = "amixer sget Master"
            result = await _run_command(command)
            if result:
                # Find the line with percentage volume
                for line in result.split('\n'):
                    if 'Playback' in line and '%' in line: # Common format line
                        try:
                            # Extract percentage value like [80%]
                            level_str = line.split('[')[1].split('%]')[0]
                            # Check if muted like [off]
                            muted = '[off]' in line.lower()
                            if muted:
                                return f"Current Volume: Muted ({level_str}%) (Linux)"
                            else:
                                return f"Current Volume: {level_str}% (Linux)"
                        except IndexError:
                            continue # Try next line if parsing fails
                return "Error: Could not parse volume from amixer output on Linux."
            else:
                return "Error getting volume on Linux. Command: '{command}'. Is 'amixer' installed?"
        else:
            return f"Error: Volume control not supported on this platform ({sys.platform})."

    except Exception as e:
        # Catch potential errors during volume retrieval (e.g., audio device issues)
        return f"An unexpected error occurred getting volume: {e}"


def get_battery_status() -> str:
    """Gets battery percentage and charging status."""
    if psutil:
        try:
            battery = psutil.sensors_battery()
            if battery:
                percent = int(battery.percent)
                charging = battery.power_plugged
                status_text = f"Battery: {percent}% ({'Charging' if charging else 'Discharging'})"
                # Return as JSON string as in the original example
                return json.dumps({
                    "percent": percent,
                    "charging": charging,
                    "status_text": status_text
                })
            else:
                return json.dumps({"error": "No battery detected."})
        except NotImplementedError:
             return json.dumps({"error": "Battery status not supported on this system according to psutil."})
        except Exception as e:
             return json.dumps({"error": f"Error getting battery status: {e}"})
    return json.dumps({"error": "psutil library missing or failed to initialize."})


# --- Shell Command Executor (Updated success check) ---
def execute_shell_command(command):
    """
    Executes a shell command string and captures output. Returns structured dict.
    Success requires exit code 0 AND empty stderr.
    🛑 SECURITY WARNING: Only use if ALLOW_SHELL_EXECUTION is true.
    """
    result_data = {
        "command": command,
        "success": False,
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "error_message": None
    }
    if not ALLOW_SHELL_EXECUTION:
        logging.warning(f"Blocked attempt to execute shell command (disabled): {command}")
        result_data["error_message"] = "Shell command execution is disabled by server configuration."
        return result_data

    logging.warning(f"🛑 EXECUTING SHELL COMMAND: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True, # Necessary for complex commands, increases risk
            capture_output=True,
            text=True,
            timeout=SHELL_COMMAND_TIMEOUT,
            check=False
        )
        result_data["exit_code"] = result.returncode
        result_data["stdout"] = result.stdout.strip() if result.stdout else ""
        result_data["stderr"] = result.stderr.strip() if result.stderr else ""
        # --- MODIFIED SUCCESS CHECK ---
        # Success only if exit code is 0 AND stderr is empty
        result_data["success"] = (result.returncode == 0 and not result.stderr)

        if result_data["success"]:
             logging.info(f"Shell command executed successfully. Exit Code: 0, Stderr: (empty)")
        else:
             logging.warning(f"Shell command failed. Success Criteria Failed (Exit Code: {result.returncode}, Stderr: '{result.stderr[:100]}...')") # Log snippet of stderr

        return result_data

    except subprocess.TimeoutExpired:
        logging.error(f"Shell command timed out: {command}")
        result_data["error_message"] = f"Command timed out after {SHELL_COMMAND_TIMEOUT} seconds."
        result_data["success"] = False # Ensure success is false on timeout
        return result_data
    except Exception as e:
        logging.exception(f"Error executing shell command '{command}': {e}")
        result_data["error_message"] = f"Error executing shell command: {e}"
        result_data["success"] = False # Ensure success is false on other exceptions
        return result_data


# --- Intent to Function Mapping (Unchanged) ---
INTENT_HANDLERS = {
    "set_brightness": set_brightness, "toggle_wifi": toggle_wifi, "toggle_bluetooth": toggle_bluetooth,
    "get_cpu_usage": get_cpu_usage, "get_memory_usage": get_memory_usage,
    "set_volume": set_volume, "get_volume": get_volume, "get_battery_status": get_battery_status,
    "run_shell_command": execute_shell_command,
    "get_wifi_status": get_wifi_status,
    "get_bluetooth_status": get_bluetooth_status,
}


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("Server stopped manually.")
    except Exception as e: logging.exception("Server failed to start or encountered a critical error.")

