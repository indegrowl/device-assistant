    "use client"
    import React, { useState, useEffect, useRef, useCallback } from 'react';
    // Icons (Removed Clock from import)
    import { Wifi, Bluetooth, Sun, SunDim, Volume, Volume1, Volume2, VolumeX, Battery, BatteryCharging, Cpu, MemoryStick } from 'lucide-react';
    // Recharts components
    import {
        LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
    } from 'recharts';

    // --- WebSocket Configuration ---
    const WEBSOCKET_URL = "ws://localhost:8765"; // Ensure this matches your Python server
    const HISTORY_LENGTH = 60; // Number of data points to keep for the graph
    const UPDATE_INTERVAL_MS = 1000; // Update interval in milliseconds

    // --- Helper Function for Volume Icon (Used inside slider now) ---
    const VolumeIcon = ({ level }) => {
        // Consistent size for icons inside slider
        const iconSize = "w-6 h-6"; // Or adjust as needed
        const strokeWidth = 1.5;
        if (level === null || typeof level === 'undefined') return <Volume className={`${iconSize} text-gray-700`} strokeWidth={strokeWidth} />;
        if (level === 0) return <VolumeX className={`${iconSize} text-gray-700`} strokeWidth={strokeWidth} />;
        if (level < 33) return <Volume className={`${iconSize} text-gray-700`} strokeWidth={strokeWidth} />;
        if (level < 66) return <Volume1 className={`${iconSize} text-gray-700`} strokeWidth={strokeWidth} />;
        return <Volume2 className={`${iconSize} text-gray-700`} strokeWidth={strokeWidth} />;
    };

    // --- Function to initialize graph data ---
    const initializeGraphData = () => {
        return Array.from({ length: HISTORY_LENGTH }, (_, i) => ({
            name: i,
            cpu: null,
            mem: null,
        }));
    };

    // --- *** Custom Vertical Fill Slider Component (Boxy Style) *** ---
    const VerticalFillSlider = React.memo(({
        value,        // Current value (0-100)
        onChange,     // Callback for value change during interaction
        onInteractionEnd, // Callback when interaction (drag/click) ends
        iconElement,  // React element for the icon
        ariaLabel     // Accessibility label
    }) => {
        const sliderRef = useRef(null);
        const isDragging = useRef(false);

        const clampValue = (val) => Math.max(0, Math.min(100, val));

        const calculateValueFromY = useCallback((clientY) => {
            if (!sliderRef.current) return 0;
            const rect = sliderRef.current.getBoundingClientRect();
            if (rect.height === 0) return 0;
            const offsetY = clientY - rect.top;
            const height = rect.height;
            let percentage = ((height - offsetY) / height) * 100;
            return clampValue(Math.round(percentage));
        }, []);

        const handleInteractionStart = useCallback((event) => {
            if (event.button === 2) return;
            event.preventDefault();
            isDragging.current = true;
            const clientY = event.type === 'touchstart' ? event.touches[0].clientY : event.clientY;
            const newValue = calculateValueFromY(clientY);
            if (onChange) onChange(newValue);
            window.addEventListener('mousemove', handleInteractionMove);
            window.addEventListener('touchmove', handleInteractionMove, { passive: false });
            window.addEventListener('mouseup', handleInteractionEnd);
            window.addEventListener('touchend', handleInteractionEnd);
        }, [calculateValueFromY, onChange]); // handleInteractionMove, handleInteractionEnd added below

        const handleInteractionMove = useCallback((event) => {
            if (!isDragging.current) return;
            event.preventDefault();
            const clientY = event.type === 'touchmove' ? event.touches[0].clientY : event.clientY;
            const newValue = calculateValueFromY(clientY);
            if (onChange) onChange(newValue);
        }, [calculateValueFromY, onChange]);

        const handleInteractionEnd = useCallback(() => {
            if (!isDragging.current) return;
            isDragging.current = false;
            window.removeEventListener('mousemove', handleInteractionMove);
            window.removeEventListener('touchmove', handleInteractionMove);
            window.removeEventListener('mouseup', handleInteractionEnd);
            window.removeEventListener('touchend', handleInteractionEnd);
            if (onInteractionEnd) onInteractionEnd();
        }, [onInteractionEnd, handleInteractionMove]);

        useEffect(() => {
            // Effect to ensure latest callbacks are used in listeners
        }, [handleInteractionMove, handleInteractionEnd]);

        const fillHeight = `${clampValue(value)}%`;

        return (
            <div
                ref={sliderRef}
                className="relative w-16 h-full bg-black/20 rounded-xl overflow-hidden cursor-ns-resize touch-none select-none"
                onMouseDown={handleInteractionStart}
                onTouchStart={handleInteractionStart}
                role="slider" aria-valuemin="0" aria-valuemax="100" aria-valuenow={value} aria-label={ariaLabel} aria-orientation="vertical" tabIndex={0}
            >
                <div className="absolute bottom-0 left-0 right-0 bg-white/90 rounded-b-xl" style={{ height: fillHeight }}>
                    {iconElement && (<div className="absolute bottom-2 left-1/2 transform -translate-x-1/2">{iconElement}</div>)}
                </div>
            </div>
        );
    });
    VerticalFillSlider.displayName = 'VerticalFillSlider';


    // --- Main App Component ---
    function App() {
    // --- State Variables ---
    const [isConnected, setIsConnected] = useState(false);
    const [commandInput, setCommandInput] = useState('');
    const [brightness, setBrightness] = useState(75);
    const [volume, setVolume] = useState(50);
    const [isWifiOn, setIsWifiOn] = useState(false);
    const [isBluetoothOn, setIsBluetoothOn] = useState(false);
    const [wifiToggleInProgress, setWifiToggleInProgress] = useState(false);
    const [bluetoothToggleInProgress, setBluetoothToggleInProgress] = useState(false);
    const [cpuUsage, setCpuUsage] = useState('- %');
    const [memoryUsage, setMemoryUsage] = useState('- %');
    const [batteryPercent, setBatteryPercent] = useState(null);
    const [isCharging, setIsCharging] = useState(false);
    const [usageHistory, setUsageHistory] = useState(initializeGraphData());
    const [currentTime, setCurrentTime] = useState(new Date());

    // Refs
    const ws = useRef(null);
    const reconnectTimer = useRef(null);
    const requestIntervalId = useRef(null);
    const timeUpdateIntervalId = useRef(null);

    // --- Send Command ---
    const sendCommand = useCallback((command) => {
        if (ws.current && ws.current.readyState === WebSocket.OPEN) {
        if (!command.includes("usage") && !command.includes("get_volume") && !command.includes("get_battery_status")) {
            // console.log("Sending command:", command);
        }
        ws.current.send(command);
        } else {
        if (!command.includes("usage") && !command.includes("get_volume") && !command.includes("get_battery_status")) {
            console.warn("WebSocket is not connected. Cannot send command:", command);
        }
        }
    }, []);

    // --- Stop Request Interval ---
    const stopRequestInterval = () => {
        if (requestIntervalId.current) {
            clearInterval(requestIntervalId.current);
            requestIntervalId.current = null;
        }
    };

    // --- Get initial status of WiFi and Bluetooth ---
    const fetchInitialStatus = useCallback(() => {
        sendCommand("get wifi status");
        sendCommand("get bluetooth status");
    }, [sendCommand]);

    // --- WebSocket Connection Logic ---
    const connectWebSocket = useCallback(() => {
        console.log("Attempting to connect to WebSocket...");
        console.log('[Status]: Connecting...');
        setIsConnected(false);
        stopRequestInterval();
        if (ws.current && ws.current.readyState !== WebSocket.CLOSED) ws.current.close();
        if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
        ws.current = new WebSocket(WEBSOCKET_URL);
        ws.current.onopen = () => {
        console.log("WebSocket connection established."); 
        setIsConnected(true); 
        console.log('[Status]: Connected.'); 
        setUsageHistory(initializeGraphData()); 
        stopRequestInterval();
        
        // Get initial status of all components
        fetchInitialStatus();
        sendCommand("get cpu usage"); 
        sendCommand("get memory usage"); 
        sendCommand("get_volume"); 
        sendCommand("get_battery_status");
        
        requestIntervalId.current = setInterval(() => { 
            sendCommand("get cpu usage"); 
            sendCommand("get memory usage"); 
            sendCommand("get_volume"); 
            sendCommand("get_battery_status"); 
            sendCommand("get wifi status");
            sendCommand("get bluetooth status");
        }, UPDATE_INTERVAL_MS);
        };
        // ws.current.onmessage = (event) => {
        //     try {
        //         const data = JSON.parse(event.data); 
        //         let messageText = ''; 
        //         let currentCpu = null; 
        //         let currentMem = null;
        //         let responsePayload = data.response;
                
        //         if (typeof data.response === 'string') { 
        //             try { 
        //                 responsePayload = JSON.parse(data.response); 
        //                 messageText = responsePayload.status_text || JSON.stringify(responsePayload); 
        //             } catch (e) { 
        //                 messageText = data.response; 
        //                 responsePayload = null; 
        //             } 
        //         }
        //         else if (typeof data.response === 'object' && data.response !== null) { 
        //             responsePayload = data.response; 
        //             messageText = responsePayload.status_text || JSON.stringify(responsePayload); 
        //         }
        //         else { 
        //             messageText = String(data.response); 
        //             responsePayload = null; 
        //         }
                
        //         const lowerResponse = messageText.toLowerCase();
                
        //         // CPU Usage
        //         if (lowerResponse.includes("cpu usage:")) { 
        //             const match = lowerResponse.match(/cpu usage:.*?([\d.]+)%/); 
        //             if (match && match[1]) currentCpu = parseFloat(match[1]); 
        //             setCpuUsage(`${currentCpu?.toFixed(1) ?? '-'} %`); 
        //         }
        //         // Memory Usage
        //         else if (lowerResponse.includes("memory usage:")) { 
        //             const match = lowerResponse.match(/memory usage:.*?([\d.]+)%/); 
        //             if (match && match[1]) currentMem = parseFloat(match[1]); 
        //             setMemoryUsage(`${currentMem?.toFixed(1) ?? '-'} %`); 
        //         }
        //         // Volume
        //         else if (lowerResponse.includes("current volume:")) { 
        //             const match = lowerResponse.match(/current volume:.*?(\d+)/); 
        //             if (match && match[1]) setVolume(parseInt(match[1], 10)); 
        //         }
        //         // Battery Status
        //         else if (responsePayload && typeof responsePayload.percent === 'number') { 
        //             setBatteryPercent(responsePayload.percent); 
        //             setIsCharging(responsePayload.charging); 
        //         }
        //         else if (responsePayload && responsePayload.error) { 
        //             setBatteryPercent(null); 
        //             setIsCharging(false); 
        //             console.error('[Server Error - Battery]:', responsePayload.error); 
        //         }
        //         // Wi-Fi Status Responses
        //         else if (lowerResponse.includes("wi-fi status:") || lowerResponse.includes("wifi status:")) {
        //             // Handle explicit status response
        //             const wifiStatus = lowerResponse.includes("enabled") || lowerResponse.includes("on") || lowerResponse.includes("connected");
        //             console.log(`Setting WiFi status: ${wifiStatus ? 'ON' : 'OFF'}`);
        //             setIsWifiOn(wifiStatus);
        //             setWifiToggleInProgress(false);
        //         }
        //         // Wi-Fi Toggle Responses
        //         else if (lowerResponse.includes("wi-fi turned on") || lowerResponse.includes("wifi turned on") || 
        //                  lowerResponse.includes("wi-fi enabled") || lowerResponse.includes("wifi enabled")) { 
        //             console.log("WiFi toggled ON");
        //             setIsWifiOn(true); 
        //             setWifiToggleInProgress(false);
        //         } 
        //         else if (lowerResponse.includes("wi-fi turned off") || lowerResponse.includes("wifi turned off") || 
        //                  lowerResponse.includes("wi-fi disabled") || lowerResponse.includes("wifi disabled")) { 
        //             console.log("WiFi toggled OFF");
        //             setIsWifiOn(false); 
        //             setWifiToggleInProgress(false);
        //         }
        //         // Bluetooth Status Responses
        //         else if (lowerResponse.includes("bluetooth status:")) {
        //             // Handle explicit status response
        //             const bluetoothStatus = lowerResponse.includes("enabled") || lowerResponse.includes("on") || lowerResponse.includes("connected");
        //             console.log(`Setting Bluetooth status: ${bluetoothStatus ? 'ON' : 'OFF'}`);
        //             setIsBluetoothOn(bluetoothStatus);
        //             setBluetoothToggleInProgress(false);
        //         }
        //         // Bluetooth Toggle Responses
        //         else if (lowerResponse.includes("bluetooth turned on") || lowerResponse.includes("bluetooth enabled")) { 
        //             console.log("Bluetooth toggled ON");
        //             setIsBluetoothOn(true); 
        //             setBluetoothToggleInProgress(false);
        //         } 
        //         else if (lowerResponse.includes("bluetooth turned off") || lowerResponse.includes("bluetooth disabled")) { 
        //             console.log("Bluetooth toggled OFF");
        //             setIsBluetoothOn(false); 
        //             setBluetoothToggleInProgress(false);
        //         }
        //         // Other responses
        //         else { 
        //             if (messageText && !lowerResponse.includes("usage:") && !lowerResponse.includes("current volume:") && 
        //                 !(responsePayload && typeof responsePayload.percent === 'number')) { 
        //                 console.log('[Server]:', messageText); 
        //             } 
        //         }
                
        //         // Update graph data
        //         if (currentCpu !== null || currentMem !== null) {
        //             setUsageHistory(prevHistory => {
        //                 const lastDataPoint = Array.isArray(prevHistory) && prevHistory.length > 0 
        //                     ? prevHistory[prevHistory.length - 1] 
        //                     : { name: -1, cpu: null, mem: null }; 
        //                 const newIndex = (lastDataPoint.name ?? -1) + 1;
        //                 const cpuValue = currentCpu ?? lastDataPoint.cpu ?? null; 
        //                 const memValue = currentMem ?? lastDataPoint.mem ?? null;
                        
        //                 if (typeof cpuValue === 'number' || typeof memValue === 'number') { 
        //                     const newDataPoint = { name: newIndex, cpu: cpuValue, mem: memValue }; 
        //                     const updatedHistory = [...(Array.isArray(prevHistory) ? prevHistory : []), newDataPoint]; 
        //                     return updatedHistory.slice(-HISTORY_LENGTH); 
        //                 }
        //                 return Array.isArray(prevHistory) ? prevHistory : [];
        //             }); 
        //         }
        //     } catch (e) { 
        //         console.error("Failed to parse message or process response:", event.data, e); 
        //         console.error('[Error]: Received unprocessable data:', event.data); 
        //     }
        // };
        ws.current.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data); 
                let messageText = ''; 
                let currentCpu = null; 
                let currentMem = null;
                let responsePayload = data.response;
                
                if (typeof data.response === 'string') { 
                    try { 
                        responsePayload = JSON.parse(data.response); 
                        messageText = responsePayload.status_text || JSON.stringify(responsePayload); 
                    } catch (e) { 
                        messageText = data.response; 
                        responsePayload = null; 
                    } 
                }
                else if (typeof data.response === 'object' && data.response !== null) { 
                    responsePayload = data.response; 
                    messageText = responsePayload.status_text || JSON.stringify(responsePayload); 
                }
                else { 
                    messageText = String(data.response); 
                    responsePayload = null; 
                }
                
                const lowerResponse = messageText.toLowerCase();
                
                // CPU Usage
                if (lowerResponse.includes("cpu usage:")) { 
                    const match = lowerResponse.match(/cpu usage:.*?([\d.]+)%/); 
                    if (match && match[1]) currentCpu = parseFloat(match[1]); 
                    setCpuUsage(`${currentCpu?.toFixed(1) ?? '-'} %`); 
                }
                // Memory Usage
                else if (lowerResponse.includes("memory usage:")) { 
                    const match = lowerResponse.match(/memory usage:.*?([\d.]+)%/); 
                    if (match && match[1]) currentMem = parseFloat(match[1]); 
                    setMemoryUsage(`${currentMem?.toFixed(1) ?? '-'} %`); 
                }
                // Volume
                else if (lowerResponse.includes("current volume:")) { 
                    const match = lowerResponse.match(/current volume:.*?(\d+)/); 
                    if (match && match[1]) setVolume(parseInt(match[1], 10)); 
                }
                // Battery Status
                else if (responsePayload && typeof responsePayload.percent === 'number') { 
                    setBatteryPercent(responsePayload.percent); 
                    setIsCharging(responsePayload.charging); 
                }
                else if (responsePayload && responsePayload.error) { 
                    setBatteryPercent(null); 
                    setIsCharging(false); 
                    console.error('[Server Error - Battery]:', responsePayload.error); 
                }
        
                // ←———— New Wi‑Fi STATUS Responses ————→
                else if (
                lowerResponse.includes("wi‑fi status") ||
                lowerResponse.includes("wifi status")
                ) {
                    // Backend replied "Wi‑Fi Status: on" or "Wi‑Fi Status: off"
                    const wifiStatus =
                    lowerResponse.includes("enabled") ||
                    lowerResponse.includes("on") ||
                    lowerResponse.includes("connected");
                    setIsWifiOn(wifiStatus);
                    setWifiToggleInProgress(false);
                }
        
                // Wi‑Fi Toggle Responses
                else if (
                lowerResponse.includes("wi-fi turned on") ||
                lowerResponse.includes("wifi turned on") ||
                lowerResponse.includes("wi-fi enabled") ||
                lowerResponse.includes("wifi enabled")
                ) { 
                    console.log("WiFi toggled ON");
                    setIsWifiOn(true); 
                    setWifiToggleInProgress(false);
                } 
                else if (
                lowerResponse.includes("wi-fi turned off") ||
                lowerResponse.includes("wifi turned off") ||
                lowerResponse.includes("wi-fi disabled") ||
                lowerResponse.includes("wifi disabled")
                ) { 
                    console.log("WiFi toggled OFF");
                    setIsWifiOn(false); 
                    setWifiToggleInProgress(false);
                }
        
                // ←———— New Bluetooth STATUS Responses ————→
                else if (lowerResponse.includes("bluetooth status:")) {
                    // Backend replied "Bluetooth Status: on" or "Bluetooth Status: off"
                    const bluetoothStatus =
                    lowerResponse.includes("enabled") ||
                    lowerResponse.includes("on") ||
                    lowerResponse.includes("connected");
                    setIsBluetoothOn(bluetoothStatus);
                    setBluetoothToggleInProgress(false);
                }
        
                // Bluetooth Toggle Responses
                else if (
                lowerResponse.includes("bluetooth turned on") ||
                lowerResponse.includes("bluetooth enabled")
                ) { 
                    console.log("Bluetooth toggled ON");
                    setIsBluetoothOn(true); 
                    setBluetoothToggleInProgress(false);
                } 
                else if (
                lowerResponse.includes("bluetooth turned off") ||
                lowerResponse.includes("bluetooth disabled")
                ) { 
                    console.log("Bluetooth toggled OFF");
                    setIsBluetoothOn(false); 
                    setBluetoothToggleInProgress(false);
                }
        
                // Other responses
                else { 
                    if (
                    messageText &&
                    !lowerResponse.includes("usage:") &&
                    !lowerResponse.includes("current volume:") &&
                    !(responsePayload && typeof responsePayload.percent === 'number')
                    ) { 
                        console.log('[Server]:', messageText); 
                    } 
                }
                
                // Update graph data
                if (currentCpu !== null || currentMem !== null) {
                    setUsageHistory(prevHistory => {
                        const lastDataPoint =
                        Array.isArray(prevHistory) && prevHistory.length > 0
                            ? prevHistory[prevHistory.length - 1]
                            : { name: -1, cpu: null, mem: null };
                        const newIndex = (lastDataPoint.name ?? -1) + 1;
                        const cpuValue = currentCpu ?? lastDataPoint.cpu ?? null;
                        const memValue = currentMem ?? lastDataPoint.mem ?? null;
                        
                        if (typeof cpuValue === 'number' || typeof memValue === 'number') {
                            const newDataPoint = { name: newIndex, cpu: cpuValue, mem: memValue };
                            const updatedHistory = [...(Array.isArray(prevHistory) ? prevHistory : []), newDataPoint];
                            return updatedHistory.slice(-HISTORY_LENGTH);
                        }
                        return Array.isArray(prevHistory) ? prevHistory : [];
                    });
                }
            } catch (e) { 
                console.error("Failed to parse message or process response:", event.data, e); 
                console.error('[Error]: Received unprocessable data:', event.data); 
            }
        };
        ws.current.onerror = (event) => { 
            console.error("WebSocket error:", event); 
            console.error('[Error]: WebSocket error occurred.'); 
            stopRequestInterval(); 
        };
        ws.current.onclose = (event) => {
        console.log("WebSocket connection closed:", event.code, event.reason); 
        setIsConnected(false); 
        stopRequestInterval(); 
        setCpuUsage('- %'); 
        setMemoryUsage('- %'); 
        setUsageHistory(initializeGraphData()); 
        setBatteryPercent(null); 
        setIsCharging(false); 
        setVolume(50); 
        ws.current = null;
        
        if (!event.wasClean) { 
            console.log('[Status]: Disconnected. Retrying...'); 
            if (!reconnectTimer.current) { 
                reconnectTimer.current = setTimeout(connectWebSocket, 5000); 
            } 
        } else { 
            console.log('[Status]: Disconnected.'); 
        }
        };
    }, [sendCommand, fetchInitialStatus]);

    useEffect(() => { 
        connectWebSocket(); 
        return () => { 
        stopRequestInterval(); 
        if (reconnectTimer.current) clearTimeout(reconnectTimer.current); 
        if (ws.current) { 
            ws.current.close(1000, "Component unmounting"); 
            ws.current = null; 
        } 
        }; 
    }, [connectWebSocket]);
    
    useEffect(() => { 
        timeUpdateIntervalId.current = setInterval(() => { 
        setCurrentTime(new Date()); 
        }, 1000); 
        
        return () => { 
        if (timeUpdateIntervalId.current) { 
            clearInterval(timeUpdateIntervalId.current); 
            timeUpdateIntervalId.current = null; 
        } 
        }; 
    }, []);

    // --- Event Handlers ---
    const handleSendCommand = () => { 
        const command = commandInput.trim(); 
        if (command) { 
        sendCommand(command); 
        setCommandInput(''); 
        } 
    };
    
    const handleInputChange = (event) => { 
        setCommandInput(event.target.value); 
    };
    
    const handleInputKeyPress = (event) => { 
        if (event.key === 'Enter') handleSendCommand(); 
    };
    
    const handleWifiToggle = () => { 
        if (wifiToggleInProgress) return; // Prevent multiple rapid clicks
        
        setWifiToggleInProgress(true);
        const command = isWifiOn ? "toggle wifi off" : "toggle wifi on"; 
        sendCommand(command);
        
        // Fallback in case we don't get a response within 3 seconds
        setTimeout(() => {
        setWifiToggleInProgress(false);
        }, 3000);
    };
    
    const handleBluetoothToggle = () => { 
        if (bluetoothToggleInProgress) return; // Prevent multiple rapid clicks
        
        setBluetoothToggleInProgress(true);
        const command = isBluetoothOn ? "toggle bluetooth off" : "toggle bluetooth on"; 
        sendCommand(command);
        
        // Fallback in case we don't get a response within 3 seconds
        setTimeout(() => {
        setBluetoothToggleInProgress(false);
        }, 3000);
    };
    
    const handleBrightnessChange = (newValue) => { 
        setBrightness(newValue); 
    };
    
    const handleVolumeChange = (newValue) => { 
        setVolume(newValue); 
    };
    
    const handleBrightnessEnd = () => { 
        sendCommand(`set brightness ${brightness}`); 
    };
    
    const handleVolumeEnd = () => { 
        sendCommand(`set volume ${volume}`); 
    };

    // --- Render ---
    return (
        <>
        <div className="flex items-center justify-center min-h-screen p-4 font-inter bg-gradient-to-br from-blue-200 via-indigo-200 to-purple-200">
            <div className="w-full max-w-3xl">
            <div className="bg-white/60 backdrop-blur-xl rounded-3xl shadow-lg p-6 text-gray-800">

                {/* --- Updated Header Row --- */}
                <div className="flex justify-between items-center mb-5"> {/* Use flexbox for inline layout */}
                    {/* Left: Larger Clock (No Icon) */}
                    <div className="text-xl font-semibold text-gray-700 w-24 text-left"> {/* Increased size, added width for balance */}
                        {currentTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </div>

                    {/* Center: Title */}
                    <h1 className="text-xl font-semibold text-center text-gray-700 flex-grow"> {/* Removed mb, added flex-grow */}
                        Device Assistant
                    </h1>

                    {/* Right: Connection Status */}
                    <div className="flex items-center justify-end w-24"> {/* Added width for balance */}
                        <span className={`h-3 w-3 rounded-full mr-2 ${ isConnected ? 'bg-green-500' : 'bg-red-500 animate-pulse-fast' }`} title={isConnected ? 'Connected' : 'Disconnected'}></span>
                        <span className="text-xs font-medium text-gray-600">{isConnected ? 'Connected' : 'Disconnected'}</span>
                    </div>
                </div>
                {/* --- End of Updated Header Row --- */}


                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5 md:items-stretch">
                    {/* Column 1: Toggles & Info */}
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-3">
                            <button 
                                onClick={handleWifiToggle} 
                                disabled={wifiToggleInProgress}
                                className={`toggle-button 
                                    ${isWifiOn ? 'bg-indigo-600 text-white' : 'bg-gray-700/20 hover:bg-gray-700/30'} 
                                    ${wifiToggleInProgress ? 'opacity-70 cursor-not-allowed' : 'cursor-pointer'}
                                    text-gray-800 rounded-xl p-3 flex flex-col items-center justify-center aspect-square transition-colors duration-200`}
                            > 
                                <Wifi className={`w-6 h-6 mb-1 ${isWifiOn ? 'text-white' : 'text-gray-800'} 
                                    ${wifiToggleInProgress ? 'animate-pulse' : ''}`} strokeWidth={1.5} /> 
                                <span className={`text-xs font-medium ${isWifiOn ? 'text-white' : ''}`}>
                                    {wifiToggleInProgress ? 'Updating...' : 'Wi-Fi'}
                                </span> 
                            </button>
                            
                            <button 
                                onClick={handleBluetoothToggle} 
                                disabled={bluetoothToggleInProgress}
                                className={`toggle-button 
                                    ${isBluetoothOn ? 'bg-indigo-600 text-white' : 'bg-gray-700/20 hover:bg-gray-700/30'} 
                                    ${bluetoothToggleInProgress ? 'opacity-70 cursor-not-allowed' : 'cursor-pointer'}
                                    text-gray-800 rounded-xl p-3 flex flex-col items-center justify-center aspect-square transition-colors duration-200`}
                            > 
                                <Bluetooth className={`w-6 h-6 mb-1 ${isBluetoothOn ? 'text-white' : 'text-gray-800'}
                                    ${bluetoothToggleInProgress ? 'animate-pulse' : ''}`} strokeWidth={1.5} /> 
                                <span className={`text-xs font-medium ${isBluetoothOn ? 'text-white' : ''}`}>
                                    {bluetoothToggleInProgress ? 'Updating...' : 'Bluetooth'}
                                </span> 
                            </button>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="bg-gray-700/10 rounded-xl p-3 flex flex-col items-center justify-center aspect-square"> 
                                {isCharging ? (
                                    <BatteryCharging className="w-6 h-6 mb-1 text-lime-600" strokeWidth={1.5} />
                                ) : (
                                    <Battery className="w-6 h-6 mb-1 text-gray-700" strokeWidth={1.5} />
                                )} 
                                <span className="text-xs font-medium mt-1">Battery</span> 
                                <span className="text-sm font-semibold">{batteryPercent !== null ? `${batteryPercent}%` : 'N/A'}</span> 
                            </div>
                            <div className="bg-gray-700/10 rounded-lg p-3 flex flex-col items-center justify-center aspect-square"> 
                                <div className="flex items-center justify-center space-x-3 w-full mb-1"> 
                                    <Cpu className="w-5 h-5 text-blue-600" strokeWidth={1.5}/> 
                                    <MemoryStick className="w-5 h-5 text-lime-600" strokeWidth={1.5}/> 
                                </div> 
                                <span className="text-xs font-medium text-gray-600 mb-1">CPU / Mem</span> 
                                <div className="flex justify-around w-full text-center"> 
                                    <span className="text-sm font-semibold text-blue-700">{cpuUsage}</span> 
                                    <span className="text-sm font-semibold text-lime-700">{memoryUsage}</span> 
                                </div> 
                            </div>
                        </div>
                    </div>

                    {/* Column 2: Custom Vertical Sliders */}
                    <div className="flex justify-around items-center gap-4 h-48 md:h-auto">
                        <VerticalFillSlider 
                            value={brightness} 
                            onChange={handleBrightnessChange} 
                            onInteractionEnd={handleBrightnessEnd} 
                            iconElement={<Sun className="w-6 h-6 text-yellow-600" strokeWidth={2}/>} 
                            ariaLabel="Brightness control" 
                        />
                        <VerticalFillSlider 
                            value={volume} 
                            onChange={handleVolumeChange} 
                            onInteractionEnd={handleVolumeEnd} 
                            iconElement={<VolumeIcon level={volume} />} 
                            ariaLabel="Volume control" 
                        />
                    </div>

                    {/* Column 3: Usage Graph */}
                    <div className="h-48 md:h-full bg-gray-700/10 rounded-lg p-2 pr-4">
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={usageHistory} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
                                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3}/>
                                <XAxis dataKey="name" tick={false} />
                                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#4b5563' }} width={30}/>
                                <Tooltip contentStyle={{ backgroundColor: 'rgba(40,40,40,0.8)', border: 'none', borderRadius: '4px', fontSize: '12px' }} itemStyle={{ color: '#eee' }} labelStyle={{ color: '#ccc', fontSize: '10px' }}/>
                                <Legend wrapperStyle={{fontSize: "10px"}}/>
                                <Line type="monotone" dataKey="cpu" stroke="#3b82f6" strokeWidth={2} dot={false} name="CPU %" animationDuration={300} connectNulls={true} />
                                <Line type="monotone" dataKey="mem" stroke="#84cc16" strokeWidth={2} dot={false} name="Mem %" animationDuration={300} connectNulls={true} />
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Bottom Row: Command Input Only */}
                <div className="grid grid-cols-1 gap-4">
                    <div>
                        <label htmlFor="command-input" className="block text-sm font-medium text-gray-700 mb-1">Send Command:</label>
                        <div className="flex space-x-2">
                            <input 
                                type="text" 
                                id="command-input" 
                                value={commandInput} 
                                onChange={handleInputChange} 
                                onKeyPress={handleInputKeyPress} 
                                className="flex-grow px-3 py-2 bg-white/80 border border-gray-300/50 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition duration-200" 
                                placeholder="e.g., Set volume to 60"
                            />
                            <button 
                                onClick={handleSendCommand} 
                                className="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-2 px-4 rounded-lg transition duration-200"
                            >
                                Send
                            </button>
                        </div>
                    </div>
                </div>

            </div>
            </div>
        </div>
        </>
    );
    }

    export default App;
