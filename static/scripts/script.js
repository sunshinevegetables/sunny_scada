// Fetch and parse YAML configuration data for compressors
async function fetchCompressorConfig(type) {
    const fileMap = {
        viltor: "viltor_comp_config.yaml",
        screw: "screw_comp_config.yaml"
    };

    const fileName = fileMap[type];
    if (!fileName) {
        showMessage(`Error: Unknown configuration type '${type}'`, "error");
        return [];
    }

    try {
        const response = await fetch(`/config/${fileName}`);
        if (!response.ok) {
            throw new Error(`Failed to load configuration for ${type}`);
        }

        const yamlText = await response.text(); // Get YAML as text
        return jsyaml.load(yamlText); // Parse YAML into a JavaScript object
    } catch (error) {
        showMessage(`Error fetching configuration: ${error.message}`, "error");
        return [];
    }
}

// Fetch and parse YAML data points dynamically based on the compressor type
async function fetchDataPoints(type) {
    const fileMap = {
        viltor: "viltor_comp_write_points.yaml",
        screw: "screw_comp_write_points.yaml"
    };

    const fileName = fileMap[type];
    if (!fileName) {
        showMessage(`Error: Unknown data points type '${type}'`, "error");
        return {};
    }

    try {
        const response = await fetch(`/config/${fileName}`);
        if (!response.ok) {
            throw new Error(`Failed to load data points for ${type}`);
        }

        const yamlText = await response.text(); // Get YAML as text
        return jsyaml.load(yamlText); // Parse YAML into a JavaScript object
    } catch (error) {
        showMessage(`Error fetching data points: ${error.message}`, "error");
        return {};
    }
}

// Fetch YAML data for alarm configurations
async function fetchAlarmConfig() {
    try {
        const response = await fetch("/config/digital_points.yaml");
        if (!response.ok) {
            throw new Error("Failed to load alarm configuration");
        }
        const yamlText = await response.text(); // Get YAML as text
        return jsyaml.load(yamlText); // Parse YAML into JavaScript object
    } catch (error) {
        console.error("Error fetching alarm config:", error.message);
        return {};
    }
}

// Function to fetch PLC data continuously
async function fetchAndDisplayPLCData(type) {
    const pollingInterval = 5000; // Polling interval in milliseconds

    try {
        // Fetch PLC data from the server
        const response = await fetch('/plc_data');
        if (!response.ok) {
            throw new Error('Failed to fetch PLC data.');
        }

        const plcData = await response.json();
        console.log("Fetched PLC Data:", plcData);

        // Display PLC data and alarms
        displayPLCData(type, plcData);
        updateAlarmTables(type, plcData); // Pass fetched PLC data to the alarm updater
    } catch (error) {
        console.error('Error fetching PLC data:', error.message);
        showMessage(`Error: ${error.message}`, 'error');
    } finally {
        // Schedule the next fetch
        setTimeout(() => fetchAndDisplayPLCData(type), pollingInterval);
    }
}

// Function to display PLC data
function displayPLCData(type, data) {
    // Check if "Main PLC" and "data" exist in the response
    if (!data["Main PLC"] || !data["Main PLC"].data) {
        console.error("Invalid data structure: Missing 'Main PLC' or 'data' key.");
        showMessage("Error: Invalid data structure received.", "error");
        return;
    }

    const plcData = data["Main PLC"].data; // Extract the "data" object
    const compressorSections = document.querySelectorAll(".compressor-section");

    compressorSections.forEach((section, index) => {
        const compressorNumber = index + 1; // Compressors are 1-indexed
        const prefix = `COMP_${compressorNumber}_`; // Tag prefix for each compressor

        // Clear existing data
        const existingData = section.querySelector(".compressor-data");
        if (existingData) existingData.remove();

        // Create a table for displaying PLC data
        const dataTable = document.createElement("table");
        dataTable.style.width = "100%";
        dataTable.style.borderCollapse = "collapse";
        dataTable.style.marginTop = "10px";

        // Add a table header
        const headerRow = document.createElement("tr");
        headerRow.style.backgroundColor = "#007BFF"; // Blue background for header
        headerRow.style.color = "white"; // White text
        headerRow.style.textAlign = "left";

        const keyHeader = document.createElement("th");
        keyHeader.textContent = "Parameter";
        keyHeader.style.padding = "10px";
        headerRow.appendChild(keyHeader);

        const valueHeader = document.createElement("th");
        valueHeader.textContent = "Value";
        valueHeader.style.padding = "10px";
        headerRow.appendChild(valueHeader);

        dataTable.appendChild(headerRow);

        // Extract relevant data for the compressor and populate rows
        Object.keys(plcData)
            .filter((key) => key.startsWith(prefix)) // Filter keys by prefix
            .forEach((key) => {
                const value = plcData[key];

                const dataRow = document.createElement("tr");

                // Alternate row colors for better readability
                dataRow.style.backgroundColor = dataTable.rows.length % 2 === 0 ? "#f9f9f9" : "#ffffff";

                const keyCell = document.createElement("td");
                keyCell.textContent = key.replace(prefix, ""); // Remove prefix for display
                keyCell.style.padding = "10px";
                keyCell.style.border = "1px solid #ddd";

                const valueCell = document.createElement("td");
                valueCell.textContent = value;
                valueCell.style.padding = "10px";
                valueCell.style.border = "1px solid #ddd";
                valueCell.style.textAlign = "right"; // Align values to the right

                dataRow.appendChild(keyCell);
                dataRow.appendChild(valueCell);
                dataTable.appendChild(dataRow);
            });

        // Append the table to the section
        const dataContainer = document.createElement("div");
        dataContainer.classList.add("compressor-data");
        dataContainer.appendChild(dataTable);

        section.appendChild(dataContainer);
    });
}



// Function to send a write request to the server
async function sendWriteRequest(plcType, plcName, signalName, value) {
    const typeMap = {
        screw: "screw_comp",
        viltor: "viltor_comp"
    };
   
    const mappedPlcType = typeMap[plcType] || plcType; // Map to backend type or fallback to original

    const buttons = document.querySelectorAll("button");
    buttons.forEach((btn) => (btn.disabled = true));

    try {
        const response = await fetch("/write_signal", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                plc_type: mappedPlcType, // Use the mapped type
                plc_name: plcName,
                signal_name: signalName,
                value: value
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || "An error occurred while sending the request.");
        }

        const result = await response.json();
        showMessage(`Successfully sent ${signalName} command to ${plcName}.`, "success");
        return result;
    } catch (error) {
        showMessage(`Error: ${error.message}`, "error");
    } finally {
        buttons.forEach((btn) => (btn.disabled = false));
    }
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
async function generateCompressorControls(type) {
    try {
        const compressorButtonsContainer = document.getElementById(`${type}-compressor-buttons`);
        compressorButtonsContainer.innerHTML = ""; // Clear existing buttons

        const compressorsData = await fetchCompressorConfig(type);
        console.log("Compressors data:", compressorsData); // Debugging output

        // Extract the array from the object
        const compressors = compressorsData[`${type}_comp`];
        const dataPointsData = await fetchDataPoints(type); // Fetch data points
        console.log("Data points:", dataPointsData);

        const dataPoints = dataPointsData[`data_points`];
        if (!Array.isArray(compressors)) {
            throw new Error("Expected an array of compressors but got something else.");
        }

        // Dynamically generate controls
        compressors.forEach((compressor) => {
            // Create a section for each compressor
            const section = document.createElement("div");
            section.classList.add("compressor-section");
            section.style.marginBottom = "20px";

            // Add compressor name
            const title = document.createElement("h3");
            title.textContent = compressor.name;
            title.style.textAlign = "center";

            // Add image for the compressor
            const image = document.createElement("img");
            image.src = "/static/images/screw_comp.jpg"; // Path to the image
            image.alt = `${compressor.name} Image`;
            image.style.width = "100%";
            image.style.maxWidth = "500px";
            image.style.display = "block";
            image.style.margin = "0 auto";

            // Create a table for buttons
            const table = document.createElement("table");
            table.style.width = "100%";
            table.style.borderCollapse = "collapse";
            table.style.marginTop = "10px";

            const row = document.createElement("tr"); // Single row for all buttons

            // Create a button for each data point and add to the table row
            Object.entries(dataPoints).forEach(([pointName, address]) => {
                if (typeof address === "number") {
                    const cell = document.createElement("td");
                    cell.style.textAlign = "center";
                    cell.style.border = "1px solid #ddd";
                    cell.style.padding = "10px";

                    const button = document.createElement("button");
                    button.textContent = `${pointName}`;
                    button.style.padding = "10px 20px";

                    // Add event listeners for button actions
                    button.addEventListener("mousedown", (event) => {
                        event.preventDefault();
                        sendWriteRequest(type, compressor.name, pointName, 1);

                        // Send the second request after a delay
                        setTimeout(() => {
                            sendWriteRequest(type, compressor.name, pointName, 0);
                        }, 500); // Delay in milliseconds
                    });

                    // Append the button to the cell, and the cell to the row
                    cell.appendChild(button);
                    row.appendChild(cell);
                } else {
                    console.warn(`Invalid point details for ${pointName}:`, address);
                }
            });

            // Append the row to the table
            table.appendChild(row);

            // Append the title, image, and table to the section
            section.appendChild(title);
            section.appendChild(image);
            section.appendChild(table);

            // Append the section to the container
            compressorButtonsContainer.appendChild(section);
        });

        // Start continuous data fetching
        fetchAndDisplayPLCData(type);
    } catch (error) {
        console.error("Error in generateCompressorControls:", error.message);
        showMessage(`Error: ${error.message}`, "error");
    }
}

// Function to update alarm tables dynamically using PLC data
async function updateAlarmTables(type, plcData) {
    const alarmContainer = document.getElementById("alarm-container");
    alarmContainer.innerHTML = ""; // Clear any existing tables

    const alarmConfig = await fetchAlarmConfig();
    if (!alarmConfig.comp_alarm_points) {
        console.error("Invalid or empty alarm configuration");
        return;
    }

    // Iterate over each compressor in the config
    for (const [compressorName, compressorData] of Object.entries(alarmConfig.comp_alarm_points)) {
        const { address, alarms } = compressorData;

        // Find the status register value from PLC data
        const statusRegister = plcData[address] || 0; // Default to 0 if address is not found

        // Create a table for the compressor
        const tableContainer = document.createElement("div");
        tableContainer.classList.add("table-container");

        const tableTitle = document.createElement("h2");
        tableTitle.textContent = `${compressorName} Alarms`;
        tableContainer.appendChild(tableTitle);

        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const tbody = document.createElement("tbody");

        // Create table headers
        thead.innerHTML = `
            <tr>
                <th>Bit Position</th>
                <th>Description</th>
                <th>Status</th>
            </tr>
        `;

        // Populate table rows with alarms
        alarms.forEach((alarm) => {
            const { bit, description } = alarm;

            // Determine the alarm status using bitwise operation
            const isAlarmOn = (statusRegister & (1 << bit)) !== 0;

            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${bit}</td>
                <td>${description}</td>
                <td>
                    <div class="toggle-status ${isAlarmOn ? "on" : "off"}">
                        ${isAlarmOn ? "ON" : "OFF"}
                    </div>
                </td>
            `;

            tbody.appendChild(row);
        });

        table.appendChild(thead);
        table.appendChild(tbody);
        tableContainer.appendChild(table);
        alarmContainer.appendChild(tableContainer);
    }
}

// Initialize the controls
document.addEventListener("DOMContentLoaded", async () => {
    // Infer compressorType from the current URL's path
    const urlPath = window.location.pathname; // Get the path (e.g., /static/viltor.html)
    const fileName = urlPath.split("/").pop(); // Extract the file name (e.g., viltor.html)
    const compressorType = fileName.replace(".html", ""); // Remove .html to get the type (e.g., viltor or screw)

    if (!compressorType || (compressorType !== "viltor" && compressorType !== "screw")) {
        showMessage("Invalid or unknown compressor type in the URL.", "error");
        return;
    }

    console.log(`Compressor type detected: ${compressorType}`);
    await generateCompressorControls(compressorType);
});

