document.addEventListener('DOMContentLoaded', () => {
    // Attempt to sync immediately on page load in case we came back online
    flushQueue();

    // Listen for connection restoration
    window.addEventListener('online', () => {
        console.log('Back online. Attempting to sync...');
        flushQueue();
    });

    // Intercept form submissions
    document.addEventListener('submit', async (e) => {
        const form = e.target;
        
        // Only intercept forms explicitly marked for offline capabilities
        if (!form.matches('[data-offline="true"]')) return;

        if (!navigator.onLine) {
            e.preventDefault(); // Stop standard browser submission
            
            // Convert form to a JSON object
            const formData = new FormData(form);
            const payload = Object.fromEntries(formData.entries());

            // Build request object
            const queuedRequest = {
                id: Date.now(),
                url: form.action || window.location.href,
                method: (form.method || 'POST').toUpperCase(),
                body: payload,
                timestamp: new Date().toISOString()
            };

            // Save to localStorage
            const queue = JSON.parse(localStorage.getItem('erp_offline_queue') || '[]');
            queue.push(queuedRequest);
            localStorage.setItem('erp_offline_queue', JSON.stringify(queue));

            alert('You are offline. Your action has been saved and will sync automatically when you reconnect.');
            
            // Clean up UI
            form.reset();
            
            // Close any open Bootstrap modals
            const modal = bootstrap.Modal.getInstance(form.closest('.modal'));
            if (modal) modal.hide();
        }
    });
});

async function flushQueue() {
    if (!navigator.onLine) return;

    let queue = JSON.parse(localStorage.getItem('erp_offline_queue') || '[]');
    if (queue.length === 0) return;

    let syncSuccessful = false;

    for (const req of queue) {
        try {
            const response = await fetch(req.url, {
                method: req.method,
                headers: { 
                    'Content-Type': 'application/json',
                    'X-Offline-Sync': 'true' // Custom header to identify background syncs
                },
                body: JSON.stringify(req.body)
            });

            if (response.ok) {
                // Remove the synced item from the queue by filtering out the ID
                queue = queue.filter(item => item.id !== req.id);
                localStorage.setItem('erp_offline_queue', JSON.stringify(queue));
                syncSuccessful = true;
            }
        } catch (err) {
            console.error('Failed to sync item:', req.id, err);
        }
    }

    if (syncSuccessful) {
        // Refresh the page so tables update with the newly synced data
        window.location.reload();
    }
}