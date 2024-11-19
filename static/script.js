// Configuration for compressors and points (normally fetched from server-side)
const compressors = [
    { name: "Viltor Compressor 1", ip: "192.168.1.13" },
    { name: "Micro820 Comp-2 Viltor", ip: "192.168.1.15" },
    { name: "Micro820 Comp-3 Viltor", ip: "192.168.1.17" }
];

const dataPoints = {
  "COMPRESSOR START": 40200,
  "COMPRESSOR STOP": 40201,
  "LOAD MODE ENABLE": 40202,
  "UNLOAD MODE ENABLE": 40203,
  "RESET FAULT": 40204,
  "REMOTE MODE ENABLE": 40205,
  "LOCAL MODE ENABLE": 40206,
  "AUTO MODE ENABLE": 40207,
  "MANUAL MODE ENABLE": 40208,
  "SHUTDOWN MODE ENABLE": 40209
};

const compressorButtonsContainer = document.getElementById("viltor-compressor-buttons");

// Function to send a write request to the server
async function sendWriteRequest(plcType, plcName, signalName, value) {
    const response = await fetch("/write_signal", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            plc_type: plcType,
            plc_name: plcName,
            signal_name: signalName,
            value: value
        })
    });
    const result = await response.json();
    return result;
}


// Function to show a message
function showMessage(message, type) {
    const messageContainer = document.getElementById("message-container");
    messageContainer.textContent = message;
    messageContainer.className = `message ${type}`;
    setTimeout(() => {
        messageContainer.textContent = "";
        messageContainer.className = "message";
    }, 5000);
}

// Dynamically generate buttons for compressors and data points
compressors.forEach((compressor) => {
    const section = document.createElement("div");
    section.classList.add("compressor-section");
    section.innerHTML = `<h3>${compressor.name}</h3>`;

    for (const [pointName, address] of Object.entries(dataPoints)) {
        const onButton = document.createElement("button");
        onButton.textContent = `Turn ON ${pointName}`;
        onButton.onclick = () => sendWriteRequest(compressor.name, pointName, 1);

        const offButton = document.createElement("button");
        offButton.textContent = `Turn OFF ${pointName}`;
        offButton.onclick = () => sendWriteRequest(compressor.name, pointName, 0);

        const buttonContainer = document.createElement("div");
        buttonContainer.classList.add("button-container");
        buttonContainer.appendChild(onButton);
        buttonContainer.appendChild(offButton);

        section.appendChild(buttonContainer);
    }

    compressorButtonsContainer.appendChild(section);
});