// Database configuration for IndexedDB
const dbName = "TimosERP_OfflineDB";
const storeName = "offlineRequests";

// 1. Initialize and open IndexedDB
function openDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(dbName, 1);
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(storeName)) {
                // Create a store with an auto-incrementing ID
                db.createObjectStore(storeName, { keyPath: "id", autoIncrement: true });
            }
        };
        
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

// 2. Save intercepted form data to the local database
async function saveOfflineRequest(url, method, payload) {
    const db = await openDB();
    const tx = db.transaction(storeName, "readwrite");
    const store = tx.objectStore(storeName);
    
    store.add({ 
        url, 
        method, 
        payload, 
        timestamp: new Date().toISOString() 
    });
    
    return new Promise((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

// 3. Background Sync: Send saved data to Flask when online
async function syncOfflineRequests() {
    const db = await openDB();
    const tx = db.transaction(storeName, "readonly");
    const store = tx.objectStore(storeName);
    const request = store.getAll();

    request.onsuccess = async () => {
        const items = request.result;
        if (items.length === 0) return; // Nothing to sync

        console.log(`Attempting to sync ${items.length} offline actions...`);
        let syncedCount = 0;

        for (const item of items) {
            try {
                const response = await fetch(item.url, {
                    method: item.method,
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Offline-Sync": "true" // Tells Flask this is a background sync
                    },
                    body: new URLSearchParams(item.payload).toString()
                });

                if (response.ok) {
                    // If Flask accepts it, delete it from the local database
                    const deleteTx = db.transaction(storeName, "readwrite");
                    deleteTx.objectStore(storeName).delete(item.id);
                    syncedCount++;
                }
            } catch (error) {
                console.error("Failed to sync item. Server might still be unreachable.", error);
            }
        }
        
        if (syncedCount > 0) {
            alert(`${syncedCount} offline actions have been successfully synced to the server!`);
            window.location.reload(); // Refresh to show updated data (like new inventory or customers)
        }
    };
}

// 4. Intercept Form Submissions
document.addEventListener("submit", async (e) => {
    // If the user is offline, stop the normal form submission
    if (!navigator.onLine) {
        const form = e.target;

        // Only intercept forms explicitly marked as safe to save-for-later
        // (adding a sale, debt, booking, etc). Forms that fetch or generate
        // something new (Reports, CSV import, Login) are deliberately left
        // alone — queuing those doesn't make sense, since the person needs
        // that answer right now, not "eventually, whenever they're next
        // online and happen to be looking at this tab again".
        if (!form.hasAttribute("data-offline")) return;

        // Only intercept POST requests (adding/editing data)
        if (form.method.toUpperCase() !== "POST") return;

        e.preventDefault(); // Stop page reload
        
        // Extract all data from the form
        const formData = new FormData(form);
        const data = Object.fromEntries(formData.entries());
        const url = form.action || window.location.href;

        // Save it locally
        await saveOfflineRequest(url, "POST", data);
        
        // Notify the user
        alert("You are offline. Your entry has been saved and will sync automatically when you reconnect.");
        form.reset();
        
        // Optional: If you use Bootstrap modals for forms, hide them automatically
        const openModal = document.querySelector('.modal.show');
        if (openModal && typeof bootstrap !== 'undefined') {
            const modalInstance = bootstrap.Modal.getInstance(openModal);
            if (modalInstance) modalInstance.hide();
        }
    }
});

// 5. Listeners to trigger the sync automatically
window.addEventListener("online", () => {
    console.log("Connection restored! Syncing data...");
    syncOfflineRequests();
});

// Also check if there's pending data to sync when the page first loads
window.addEventListener("load", () => {
    if (navigator.onLine) {
        syncOfflineRequests();
    }
});