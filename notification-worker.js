self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick',event=>{event.notification.close();event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{const page=windows.find(w=>new URL(w.url).origin===self.location.origin);return page?page.focus():self.clients.openWindow('/')}))});
