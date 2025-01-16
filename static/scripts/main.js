fetch('/processes')
    .then(response => response.json())
    .then(data => {
        const list = document.getElementById('process-list');
        data.forEach(process => {
            const listItem = document.createElement('li');
            listItem.textContent = process;
            list.appendChild(listItem);
        });
    })
    .catch(error => console.error('Error fetching processes:', error));
