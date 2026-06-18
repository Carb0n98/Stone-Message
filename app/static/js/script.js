/* ===================================================================
   STONE MESSAGES — Client Application
   Modular architecture for multi-room chat
   =================================================================== */

// ── Global State ──
const state = {
    socket: null,
    rooms: [],
    currentRoomId: null,
    currentFilter: 'all',
    typingUsers: {},
    typingTimeout: null,
    replyTo: null,
    editingMessage: null,
    contextMessageId: null,
    contextMessageData: null,
    allUsers: [],
    loadingMessages: false,
    currentPage: 1,
    hasMoreMessages: false,
    roomSearchQuery: '',
};

const EMOJIS = [
    '😀','😂','😍','🥰','😘','😎','🤩','🥳','😊','😇',
    '🤔','😏','😴','🤗','🤭','😱','😤','😭','🥺','😈',
    '👍','👎','❤️','🔥','⭐','🎉','💯','🙏','👋','✌️',
    '💪','👏','🤝','💀','🫡','🤡','💩','👀','🗿','🫠',
    '✅','❌','⚡','💎','🌟','🎯','🚀','💡','📌','🎵'
];

// CSRF Token for API requests
let csrfToken = '';

// ── Initialization ──
window.addEventListener('DOMContentLoaded', function() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) {
        csrfToken = meta.getAttribute('content');
    }
    initSocket();
    initTheme();
    initUI();
    initEmojiPicker();
    loadRooms();
    loadUsers();
    requestNotificationPermission();
});

// ===================================================================
// SOCKET MANAGER
// ===================================================================

function initSocket() {
    state.socket = io();

    state.socket.on('error', function(err) {
        showToast(err.message || 'Ocorreu um erro', 'error');
    });

    state.socket.on('connect', function() {
        console.log('Connected to server');
        // Re-join current room if reconnecting
        if (state.currentRoomId) {
            state.socket.emit('join_room', { room_id: state.currentRoomId });
        }
    });

    state.socket.on('room_message', function(data) {
        if (data.room_id === state.currentRoomId) {
            appendMessage(data);
            scrollToBottom();
            // Mark as read
            state.socket.emit('mark_read', { room_id: state.currentRoomId });
        }
        // Update room list
        updateRoomLastMessage(data.room_id, data);
        sendNotificationIfNeeded(data);
    });

    state.socket.on('message_edited', function(data) {
        updateMessageInDOM(data);
    });

    state.socket.on('message_deleted', function(data) {
        var el = document.querySelector('[data-message-id="' + data.id + '"]');
        if (el) {
            var bubble = el.querySelector('.message-bubble');
            if (bubble) {
                bubble.classList.add('deleted');
                bubble.innerHTML = '<em>[Mensagem apagada]</em>';
                var reactions = el.querySelector('.message-reactions');
                if (reactions) reactions.remove();
                var actions = el.querySelector('.message-actions');
                if (actions) actions.remove();
            }
        }
    });

    state.socket.on('message_pinned', function(data) {
        showToast(data.username + (data.is_pinned ? ' fixou' : ' desafixou') + ' uma mensagem', 'success');
        if (data.room_id === state.currentRoomId) {
            loadPinnedMessages();
        }
    });

    state.socket.on('reaction_updated', function(data) {
        if (data.message) {
            updateMessageReactions(data.message);
        }
    });

    state.socket.on('user_typing', function(data) {
        if (data.room_id === state.currentRoomId) {
            handleTypingIndicator(data);
        }
    });

    state.socket.on('user_status', function(data) {
        updateUserOnlineStatus(data);
    });

    state.socket.on('unread_update', function(data) {
        updateRoomUnread(data.room_id, data.unread_count);
    });

    state.socket.on('disconnect', function() {
        console.log('Disconnected');
    });
}

// ===================================================================
// THEME MANAGER
// ===================================================================

function initTheme() {
    var saved = localStorage.getItem('stone_theme') || APP_DATA.theme || 'default';
    applyTheme(saved);
    updateThemeUI(saved);
}

function setTheme(theme) {
    applyTheme(theme);
    updateThemeUI(theme);
    localStorage.setItem('stone_theme', theme);

    // Save to server
    fetch('/api/user/settings', {
        method: 'PUT',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken
        },
        body: JSON.stringify({ theme: theme })
    });
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
}

function updateThemeUI(theme) {
    document.querySelectorAll('.theme-option').forEach(function(el) {
        el.classList.toggle('active', el.getAttribute('data-theme') === theme);
    });
}

// ===================================================================
// UI MANAGER
// ===================================================================

function initUI() {
    // Message input auto-resize
    var input = document.getElementById('messageInput');
    input.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // Send on Enter (Shift+Enter for new line)
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Typing indicator
    input.addEventListener('input', function() {
        if (state.currentRoomId) {
            state.socket.emit('typing', { room_id: state.currentRoomId, is_typing: true });
            clearTimeout(state.typingTimeout);
            state.typingTimeout = setTimeout(function() {
                state.socket.emit('typing', { room_id: state.currentRoomId, is_typing: false });
            }, 2000);
        }
    });

    // Global search
    var searchInput = document.getElementById('globalSearch');
    var searchTimer;
    searchInput.addEventListener('input', function() {
        clearTimeout(searchTimer);
        var q = this.value.trim();
        searchTimer = setTimeout(function() {
            if (q.length >= 2) {
                performGlobalSearch(q);
            } else {
                filterRoomList();
            }
        }, 300);
    });

    // Close menus on click outside
    document.addEventListener('click', function(e) {
        // Close context menu
        var ctx = document.getElementById('contextMenu');
        if (ctx.classList.contains('active') && !ctx.contains(e.target)) {
            ctx.classList.remove('active');
        }
        // Close room menu
        var rm = document.getElementById('roomMenu');
        if (rm.classList.contains('active') && !rm.contains(e.target) && e.target.id !== 'roomMenuBtn') {
            rm.classList.remove('active');
        }
        // Close emoji picker
        var ep = document.getElementById('emojiPicker');
        if (ep.classList.contains('active') && !ep.contains(e.target) && e.target.id !== 'emojiBtn') {
            ep.classList.remove('active');
        }
    });

    // Scroll to load more messages
    var messagesDiv = document.getElementById('messages');
    messagesDiv.addEventListener('scroll', function() {
        if (this.scrollTop === 0 && state.hasMoreMessages && !state.loadingMessages) {
            loadMoreMessages();
        }
    });

    // Private room toggle
    document.getElementById('newRoomPrivate').addEventListener('change', function() {
        document.getElementById('memberSelectGroup').style.display = this.checked ? 'block' : 'none';
        if (this.checked) populateMemberSelect();
    });

    // Room search input
    document.getElementById('roomSearchInput').addEventListener('input', function() {
        var q = this.value.trim();
        if (q.length >= 2) {
            searchInRoom(q);
        } else {
            document.getElementById('searchResultsList').innerHTML = '';
        }
    });
}

// Sidebar visibility (mobile)
function showSidebar() {
    document.getElementById('sidebar').classList.remove('hidden');
}

function hideSidebar() {
    document.getElementById('sidebar').classList.add('hidden');
}

// Modals
function openModal(id) {
    document.getElementById(id).classList.add('active');
    if (id === 'settingsModal') {
        updateUserStorageUsageUI();
    }
}

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

function updateUserStorageUsageUI() {
    fetch('/api/user/storage')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var pct = 0;
            if (data.quota_bytes > 0) {
                pct = Math.min(100, Math.round((data.used_bytes / data.quota_bytes) * 100));
            }
            document.getElementById('userStorageProgress').style.width = pct + '%';
            document.getElementById('userStorageText').textContent = data.used_mb.toFixed(1) + ' MB / ' + data.quota_mb.toFixed(1) + ' MB';
            document.getElementById('userStoragePercent').textContent = pct + '%';
        });
}

// Toast notifications
function showToast(message, type) {
    type = type || 'success';
    var container = document.getElementById('toastContainer');
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(40px)';
        setTimeout(function() { toast.remove(); }, 300);
    }, 3000);
}

// ===================================================================
// ROOM MANAGER
// ===================================================================

function loadRooms() {
    fetch('/api/rooms')
        .then(function(r) { return r.json(); })
        .then(function(rooms) {
            state.rooms = rooms;
            renderRoomList();
        })
        .catch(function(err) {
            console.error('Error loading rooms:', err);
        });
}

function renderRoomList() {
    var list = document.getElementById('roomList');
    var filtered = filterRoomsByType(state.rooms, state.currentFilter);
    var searchQ = document.getElementById('globalSearch').value.trim().toLowerCase();

    if (searchQ) {
        filtered = filtered.filter(function(r) {
            return r.name.toLowerCase().includes(searchQ);
        });
    }

    // Sort: favorites first, then by last message
    filtered.sort(function(a, b) {
        if (a.is_favorite && !b.is_favorite) return -1;
        if (!a.is_favorite && b.is_favorite) return 1;
        var ta = a.last_message_at || '';
        var tb = b.last_message_at || '';
        return tb.localeCompare(ta);
    });

    list.innerHTML = '';
    if (filtered.length === 0) {
        list.innerHTML = '<div style="text-align:center;padding:40px 20px;color:var(--text-secondary);font-size:0.85rem;">Nenhuma conversa encontrada</div>';
        return;
    }

    filtered.forEach(function(room) {
        var item = document.createElement('div');
        item.className = 'room-item' + (room.id === state.currentRoomId ? ' active' : '');
        item.onclick = function() { selectRoom(room.id); };

        var avatarLetter = room.name.charAt(0).toUpperCase();
        var avatarColor = room.is_direct ? '#6366f1' : (room.is_private ? '#8b5cf6' : 'var(--primary-dark)');
        var icon = room.is_direct ? '👤' : (room.is_private ? '🔒' : '');

        var lastMsg = '';
        var lastTime = '';
        if (room.last_message) {
            var content = room.last_message.content || '';
            if (room.last_message.message_type === 'file') content = '📎 Arquivo';
            lastMsg = (room.last_message.username ? room.last_message.username + ': ' : '') + content;
            if (lastMsg.length > 40) lastMsg = lastMsg.substring(0, 40) + '...';
            if (room.last_message.timestamp) {
                lastTime = formatTimeShort(room.last_message.timestamp);
            }
        }

        item.innerHTML =
            '<div class="room-avatar" style="background:' + avatarColor + ';">' +
                (icon || avatarLetter) +
            '</div>' +
            '<div class="room-info">' +
                '<div class="room-name">' + escapeHtml(room.name) + '</div>' +
                '<div class="room-last-message">' + escapeHtml(lastMsg) + '</div>' +
            '</div>' +
            '<div class="room-meta">' +
                '<div class="room-time">' + lastTime + '</div>' +
                (room.unread_count > 0 ? '<div class="unread-badge">' + room.unread_count + '</div>' : '') +
            '</div>' +
            (room.is_favorite ? '<span class="favorite-indicator">⭐</span>' : '');

        list.appendChild(item);
    });
}

function filterRoomsByType(rooms, filter) {
    switch (filter) {
        case 'favorites':
            return rooms.filter(function(r) { return r.is_favorite; });
        case 'private':
            return rooms.filter(function(r) { return r.is_private || r.is_direct; });
        case 'archived':
            return rooms.filter(function(r) { return r.is_archived; });
        default:
            return rooms.filter(function(r) { return !r.is_archived; });
    }
}

function filterRooms(filter) {
    state.currentFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(function(btn) {
        btn.classList.toggle('active', btn.getAttribute('data-filter') === filter);
    });
    renderRoomList();
}

function filterRoomList() {
    renderRoomList();
}

function selectRoom(roomId) {
    state.currentRoomId = roomId;
    state.currentPage = 1;
    state.hasMoreMessages = false;

    // Update active state
    document.querySelectorAll('.room-item').forEach(function(el) {
        el.classList.remove('active');
    });

    // Show chat area, hide empty state
    document.getElementById('chatEmpty').style.display = 'none';
    var activeChat = document.getElementById('activeChat');
    activeChat.classList.remove('hidden');

    // Hide sidebar on mobile
    if (window.innerWidth <= 768) {
        hideSidebar();
    }

    // Join socket room
    state.socket.emit('join_room', { room_id: roomId });
    state.socket.emit('mark_read', { room_id: roomId });

    // Update room unread locally
    updateRoomUnread(roomId, 0);

    // Load room data
    loadRoomData(roomId);
    loadMessages(roomId);
    loadPinnedMessages();

    // Reset input state
    cancelReply();
    cancelEdit();

    // Re-render room list for active state
    renderRoomList();
}

function loadRoomData(roomId) {
    fetch('/api/rooms/' + roomId)
        .then(function(r) { return r.json(); })
        .then(function(room) {
            // Update header
            document.getElementById('chatHeaderName').textContent = room.name;
            document.getElementById('chatHeaderAvatar').textContent = room.is_direct ? '👤' : room.name.charAt(0).toUpperCase();

            var onlineCount = 0;
            if (room.members) {
                onlineCount = room.members.filter(function(m) { return m.is_online; }).length;
            }
            document.getElementById('chatHeaderStatus').textContent =
                room.member_count + ' membros' + (onlineCount > 0 ? ' · ' + onlineCount + ' online' : '');

            // Update room menu buttons
            document.getElementById('favBtnText').textContent = room.is_favorite ? '⭐ Desfavoritar' : '⭐ Favoritar';
            document.getElementById('muteBtnText').textContent = room.is_muted ? '🔔 Dessilenciar' : '🔇 Silenciar';
            document.getElementById('archiveBtnText').textContent = room.is_archived ? '📂 Desarquivar' : '📁 Arquivar';
        });
}

function createRoom() {
    var name = document.getElementById('newRoomName').value.trim();
    var desc = document.getElementById('newRoomDesc').value.trim();
    var isPrivate = document.getElementById('newRoomPrivate').checked;

    if (!name || name.length < 2) {
        showToast('Nome da sala deve ter pelo menos 2 caracteres', 'error');
        return;
    }

    var members = [];
    if (isPrivate) {
        document.querySelectorAll('#memberSelectList input:checked').forEach(function(cb) {
            members.push(parseInt(cb.value));
        });
    }

    fetch('/api/rooms', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken
        },
        body: JSON.stringify({ name: name, description: desc, is_private: isPrivate, members: members })
    })
    .then(function(r) { return r.json(); })
    .then(function(room) {
        if (room.error) {
            showToast(room.error, 'error');
            return;
        }
        closeModal('createRoomModal');
        document.getElementById('newRoomName').value = '';
        document.getElementById('newRoomDesc').value = '';
        document.getElementById('newRoomPrivate').checked = false;
        loadRooms();
        selectRoom(room.id);
        showToast('Sala "' + room.name + '" criada!', 'success');
    });
}

function updateRoomLastMessage(roomId, msgData) {
    for (var i = 0; i < state.rooms.length; i++) {
        if (state.rooms[i].id === roomId) {
            state.rooms[i].last_message = {
                content: msgData.content,
                username: msgData.username,
                timestamp: msgData.timestamp,
                message_type: msgData.message_type
            };
            state.rooms[i].last_message_at = msgData.timestamp;
            if (roomId !== state.currentRoomId) {
                state.rooms[i].unread_count = (state.rooms[i].unread_count || 0) + 1;
            }
            break;
        }
    }
    renderRoomList();
}

function updateRoomUnread(roomId, count) {
    for (var i = 0; i < state.rooms.length; i++) {
        if (state.rooms[i].id === roomId) {
            state.rooms[i].unread_count = count;
            break;
        }
    }
    renderRoomList();
}

// Room actions
function toggleFavorite() {
    if (!state.currentRoomId) return;
    fetch('/api/rooms/' + state.currentRoomId + '/favorite', { 
        method: 'PUT',
        headers: { 'X-CSRF-Token': csrfToken }
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            document.getElementById('favBtnText').textContent = data.is_favorite ? '⭐ Desfavoritar' : '⭐ Favoritar';
            showToast(data.is_favorite ? 'Adicionado aos favoritos' : 'Removido dos favoritos', 'success');
            loadRooms();
        });
    toggleRoomMenu();
}

function toggleMute() {
    if (!state.currentRoomId) return;
    fetch('/api/rooms/' + state.currentRoomId + '/mute', { 
        method: 'PUT',
        headers: { 'X-CSRF-Token': csrfToken }
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            document.getElementById('muteBtnText').textContent = data.is_muted ? '🔔 Dessilenciar' : '🔇 Silenciar';
            showToast(data.is_muted ? 'Sala silenciada' : 'Notificações ativadas', 'success');
        });
    toggleRoomMenu();
}

function toggleArchive() {
    if (!state.currentRoomId) return;
    fetch('/api/rooms/' + state.currentRoomId + '/archive', { 
        method: 'PUT',
        headers: { 'X-CSRF-Token': csrfToken }
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            showToast(data.is_archived ? 'Sala arquivada' : 'Sala desarquivada', 'success');
            loadRooms();
        });
    toggleRoomMenu();
}

function toggleRoomMenu() {
    document.getElementById('roomMenu').classList.toggle('active');
}

function togglePinnedPanel() {
    document.getElementById('pinnedPanel').classList.toggle('active');
}

function loadPinnedMessages() {
    if (!state.currentRoomId) return;
    fetch('/api/rooms/' + state.currentRoomId)
        .then(function(r) { return r.json(); })
        .then(function(room) {
            var pinnedList = document.getElementById('pinnedList');
            pinnedList.innerHTML = '';
            if (room.pinned_messages && room.pinned_messages.length > 0) {
                room.pinned_messages.forEach(function(msg) {
                    var item = document.createElement('div');
                    item.className = 'pinned-item';
                    item.innerHTML = '<span class="pinned-item-pin">📌</span>' +
                        '<strong>' + escapeHtml(msg.username) + ':</strong> ' +
                        escapeHtml(msg.content.substring(0, 80));
                    item.onclick = function() { scrollToMessage(msg.id); };
                    pinnedList.appendChild(item);
                });
            } else {
                pinnedList.innerHTML = '<div style="padding:8px;text-align:center;color:var(--text-secondary);font-size:0.82rem;">Nenhuma mensagem fixada</div>';
            }
        });
}

function showRoomInfo() {
    if (!state.currentRoomId) return;
    fetch('/api/rooms/' + state.currentRoomId)
        .then(function(r) { return r.json(); })
        .then(function(room) {
            document.getElementById('roomInfoTitle').textContent = room.name;
            var content = '<p style="color:var(--text-secondary);margin-bottom:16px;">' + escapeHtml(room.description || 'Sem descrição') + '</p>';
            content += '<div class="form-label">' + room.member_count + ' membros</div>';
            if (room.members) {
                content += '<div class="user-select-list">';
                room.members.forEach(function(m) {
                    content += '<div class="user-select-item" onclick="startDirectMessage(' + m.id + ')">' +
                        '<div class="user-avatar" style="background:' + m.avatar_color + ';width:32px;height:32px;font-size:0.75rem;">' + m.username.charAt(0).toUpperCase() + '</div>' +
                        '<span>' + escapeHtml(m.username) + '</span>' +
                        '<span style="margin-left:auto;font-size:0.72rem;color:' + (m.is_online ? 'var(--online-color)' : 'var(--text-secondary)') + ';">' +
                        (m.is_online ? '● Online' : '○ Offline') + '</span>' +
                    '</div>';
                });
                content += '</div>';
            }
            document.getElementById('roomInfoContent').innerHTML = content;
            openModal('roomInfoModal');
        });
}

function startDirectMessage(userId) {
    if (userId === APP_DATA.userId) return;
    fetch('/api/rooms/direct', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken
        },
        body: JSON.stringify({ user_id: userId })
    })
    .then(function(r) { return r.json(); })
    .then(function(room) {
        closeModal('roomInfoModal');
        loadRooms();
        setTimeout(function() { selectRoom(room.id); }, 300);
    });
}

// ===================================================================
// MESSAGE MANAGER
// ===================================================================

function loadMessages(roomId) {
    state.loadingMessages = true;
    fetch('/api/rooms/' + roomId + '/messages?page=1&per_page=50')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            state.hasMoreMessages = data.has_more;
            state.currentPage = 1;
            var messagesDiv = document.getElementById('messages');
            messagesDiv.innerHTML = '';
            renderMessages(data.messages);
            scrollToBottom();
            state.loadingMessages = false;
        });
}

function loadMoreMessages() {
    if (!state.currentRoomId || state.loadingMessages) return;
    state.loadingMessages = true;
    state.currentPage++;

    var messagesDiv = document.getElementById('messages');
    var prevHeight = messagesDiv.scrollHeight;

    fetch('/api/rooms/' + state.currentRoomId + '/messages?page=' + state.currentPage + '&per_page=50')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            state.hasMoreMessages = data.has_more;
            renderMessages(data.messages, true);
            // Maintain scroll position
            messagesDiv.scrollTop = messagesDiv.scrollHeight - prevHeight;
            state.loadingMessages = false;
        });
}

function renderMessages(messages, prepend) {
    var messagesDiv = document.getElementById('messages');
    var lastDate = '';

    messages.forEach(function(msg) {
        var msgDate = msg.timestamp ? msg.timestamp.split('T')[0] : '';
        if (msgDate !== lastDate) {
            var sep = document.createElement('div');
            sep.className = 'date-separator';
            sep.innerHTML = '<span>' + formatDate(msg.timestamp) + '</span>';
            if (prepend && messagesDiv.firstChild) {
                messagesDiv.insertBefore(sep, messagesDiv.firstChild);
            } else {
                messagesDiv.appendChild(sep);
            }
            lastDate = msgDate;
        }

        var el = createMessageElement(msg);
        if (prepend && messagesDiv.children.length > 0) {
            // Insert after date separator
            var insertAfter = messagesDiv.querySelector('.date-separator:first-child');
            if (insertAfter && insertAfter.nextSibling) {
                messagesDiv.insertBefore(el, insertAfter.nextSibling);
            } else {
                messagesDiv.insertBefore(el, messagesDiv.firstChild);
            }
        } else {
            messagesDiv.appendChild(el);
        }
    });
}

function appendMessage(msg) {
    var messagesDiv = document.getElementById('messages');
    var el = createMessageElement(msg);
    messagesDiv.appendChild(el);
}

function createMessageElement(msg) {
    var isUser = msg.username === APP_DATA.username;
    var isSystem = msg.message_type === 'system';

    var group = document.createElement('div');
    group.setAttribute('data-message-id', msg.id);

    if (isSystem) {
        group.className = 'message-group';
        group.style.alignSelf = 'center';
        group.style.maxWidth = '80%';
        var bubble = document.createElement('div');
        bubble.className = 'message-bubble system';
        bubble.textContent = msg.content;
        group.appendChild(bubble);
        return group;
    }

    group.className = 'message-group ' + (isUser ? 'user' : 'other');

    // Avatar
    var avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.style.background = msg.avatar_color || 'var(--primary-dark)';
    avatar.textContent = msg.username.charAt(0).toUpperCase();
    group.appendChild(avatar);

    // Content wrapper
    var wrapper = document.createElement('div');
    wrapper.className = 'message-content-wrapper';

    // Sender name
    var sender = document.createElement('div');
    sender.className = 'message-sender';
    sender.textContent = msg.username;
    wrapper.appendChild(sender);

    // Bubble
    var bubble = document.createElement('div');
    bubble.className = 'message-bubble' + (msg.is_deleted ? ' deleted' : '');

    // Actions (hover buttons)
    if (!msg.is_deleted) {
        var actions = document.createElement('div');
        actions.className = 'message-actions';
        actions.innerHTML =
            '<button class="msg-action-btn" onclick="startReply(' + msg.id + ')" title="Responder">↩️</button>' +
            '<button class="msg-action-btn" onclick="openReactionPicker(' + msg.id + ')" title="Reagir">😀</button>' +
            (isUser ? '<button class="msg-action-btn" onclick="startEdit(' + msg.id + ')" title="Editar">✏️</button>' : '') +
            ((isUser || APP_DATA.isAdmin) ? '<button class="msg-action-btn" onclick="deleteMessage(' + msg.id + ')" title="Excluir">🗑️</button>' : '');
        bubble.appendChild(actions);
    }

    // Reply quote
    if (msg.reply_to) {
        var quote = document.createElement('div');
        quote.className = 'message-reply-quote';
        quote.innerHTML = '<div class="reply-author">' + escapeHtml(msg.reply_to.username) + '</div>' +
            '<div>' + escapeHtml(msg.reply_to.content.substring(0, 100)) + '</div>';
        quote.onclick = function() { scrollToMessage(msg.reply_to.id); };
        bubble.appendChild(quote);
    }

    // Content
    if (msg.is_deleted) {
        bubble.innerHTML += '<em>[Mensagem apagada]</em>';
    } else if (msg.message_type === 'file' && msg.file_url) {
        // File message
        if (msg.content) {
            bubble.innerHTML += '<div>' + formatMessageContent(msg.content) + '</div>';
        }
        bubble.innerHTML += renderFilePreview(msg);
    } else {
        bubble.innerHTML += '<div>' + formatMessageContent(msg.content) + '</div>';
    }

    // Meta (time, edited)
    var meta = document.createElement('div');
    meta.className = 'message-meta';
    if (msg.is_pinned) {
        meta.innerHTML += '<span class="message-pinned-badge">📌</span>';
    }
    if (msg.edited_at) {
        meta.innerHTML += '<span class="message-edited">editada</span>';
    }
    meta.innerHTML += '<span class="message-time">' + formatTime(msg.timestamp) + '</span>';
    bubble.appendChild(meta);

    wrapper.appendChild(bubble);

    // Reactions
    if (msg.reactions && msg.reactions.length > 0) {
        var reactionsDiv = document.createElement('div');
        reactionsDiv.className = 'message-reactions';
        msg.reactions.forEach(function(r) {
            var chip = document.createElement('button');
            chip.className = 'reaction-chip' + (r.users.includes(APP_DATA.username) ? ' active' : '');
            chip.innerHTML = r.emoji + ' <span class="reaction-count">' + r.count + '</span>';
            chip.onclick = function() { toggleReaction(msg.id, r.emoji); };
            chip.title = r.users.join(', ');
            reactionsDiv.appendChild(chip);
        });
        wrapper.appendChild(reactionsDiv);
    }

    // Context menu on right-click
    group.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        showContextMenu(e, msg);
    });

    group.appendChild(wrapper);
    return group;
}

function renderFilePreview(msg) {
    var fileType = msg.file_type || '';
    var url = msg.file_url || '';
    var name = msg.file_name || 'Arquivo';
    var size = msg.file_size ? formatFileSize(msg.file_size) : '';

    if (fileType === 'image' || /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(url)) {
        return '<div class="file-preview"><img src="' + url + '" alt="' + escapeHtml(name) + '" onclick="openLightbox(\'' + url + '\')" loading="lazy"></div>';
    }
    if (fileType === 'video' || /\.(mp4|webm|mov)$/i.test(url)) {
        return '<div class="file-preview"><video controls preload="metadata"><source src="' + url + '"></video></div>';
    }
    if (fileType === 'audio' || /\.(mp3|wav|ogg|m4a)$/i.test(url)) {
        return '<div class="file-preview"><audio controls preload="metadata"><source src="' + url + '"></audio></div>';
    }

    // Generic file
    var icon = '📄';
    if (fileType === 'archive') icon = '📦';
    if (fileType === 'document') icon = '📑';
    return '<a href="' + url + '" download class="file-attachment" style="text-decoration:none;color:inherit;">' +
        '<span class="file-attachment-icon">' + icon + '</span>' +
        '<div class="file-attachment-info">' +
            '<div class="file-attachment-name">' + escapeHtml(name) + '</div>' +
            '<div class="file-attachment-size">' + size + '</div>' +
        '</div>' +
    '</a>';
}

function formatMessageContent(content) {
    if (!content) return '';
    var text = escapeHtml(content);
    // Bold
    text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Italic
    text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');
    // Links
    text = text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;">$1</a>');
    // Mentions
    text = text.replace(/@(\w+)/g, '<strong style="color:var(--primary);">@$1</strong>');
    // Newlines
    text = text.replace(/\n/g, '<br>');
    return text;
}

function updateMessageInDOM(msg) {
    var el = document.querySelector('[data-message-id="' + msg.id + '"]');
    if (!el) return;
    // Re-create the element
    var newEl = createMessageElement(msg);
    el.replaceWith(newEl);
}

function updateMessageReactions(msg) {
    var el = document.querySelector('[data-message-id="' + msg.id + '"]');
    if (!el) return;
    // Remove old reactions
    var old = el.querySelector('.message-reactions');
    if (old) old.remove();

    if (msg.reactions && msg.reactions.length > 0) {
        var wrapper = el.querySelector('.message-content-wrapper');
        var reactionsDiv = document.createElement('div');
        reactionsDiv.className = 'message-reactions';
        msg.reactions.forEach(function(r) {
            var chip = document.createElement('button');
            chip.className = 'reaction-chip' + (r.users.includes(APP_DATA.username) ? ' active' : '');
            chip.innerHTML = r.emoji + ' <span class="reaction-count">' + r.count + '</span>';
            chip.onclick = function() { toggleReaction(msg.id, r.emoji); };
            chip.title = r.users.join(', ');
            reactionsDiv.appendChild(chip);
        });
        wrapper.appendChild(reactionsDiv);
    }
}

// ===================================================================
// MESSAGE ACTIONS
// ===================================================================

function sendMessage() {
    var input = document.getElementById('messageInput');
    var content = input.value.trim();

    if (!content && !state.editingMessage) return;
    if (!state.currentRoomId) return;

    // Edit mode
    if (state.editingMessage) {
        fetch('/api/messages/' + state.editingMessage.id, {
            method: 'PUT',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
            },
            body: JSON.stringify({ content: content })
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                showToast(data.error, 'error');
            } else {
                updateMessageInDOM(data);
            }
            cancelEdit();
        });
        input.value = '';
        input.style.height = 'auto';
        return;
    }

    // Normal message
    var msgData = {
        room_id: state.currentRoomId,
        content: content,
        reply_to_id: state.replyTo ? state.replyTo.id : null,
        message_type: 'text'
    };

    state.socket.emit('room_message', msgData);

    // Optimistic local append
    var localMsg = {
        id: Date.now(),
        content: content,
        username: APP_DATA.username,
        user_id: APP_DATA.userId,
        room_id: state.currentRoomId,
        timestamp: new Date().toISOString(),
        message_type: 'text',
        is_deleted: false,
        is_pinned: false,
        edited_at: null,
        file_url: null,
        file_name: null,
        file_type: null,
        file_size: null,
        reply_to: state.replyTo ? { id: state.replyTo.id, content: state.replyTo.content, username: state.replyTo.username } : null,
        reactions: [],
        avatar_color: '#d67f9d'
    };
    appendMessage(localMsg);
    scrollToBottom();

    cancelReply();
    input.value = '';
    input.style.height = 'auto';

    // Stop typing
    state.socket.emit('typing', { room_id: state.currentRoomId, is_typing: false });
}

function startReply(messageId) {
    var el = document.querySelector('[data-message-id="' + messageId + '"]');
    if (!el) return;
    var sender = el.querySelector('.message-sender');
    var bubble = el.querySelector('.message-bubble');
    var text = bubble ? bubble.textContent.trim() : '';

    state.replyTo = {
        id: messageId,
        username: sender ? sender.textContent : 'Usuário',
        content: text.substring(0, 100)
    };

    document.getElementById('replyPreviewAuthor').textContent = state.replyTo.username;
    document.getElementById('replyPreviewText').textContent = state.replyTo.content;
    document.getElementById('replyPreview').classList.add('active');
    document.getElementById('messageInput').focus();

    // Close context menu
    document.getElementById('contextMenu').classList.remove('active');
}

function cancelReply() {
    state.replyTo = null;
    document.getElementById('replyPreview').classList.remove('active');
}

function startEdit(messageId) {
    var el = document.querySelector('[data-message-id="' + messageId + '"]');
    if (!el) return;
    var bubble = el.querySelector('.message-bubble');
    // Get raw text content (excluding actions, meta, etc.)
    var contentDiv = bubble.querySelector('div');
    var text = contentDiv ? contentDiv.textContent.trim() : '';

    state.editingMessage = { id: messageId, content: text };
    document.getElementById('editPreviewText').textContent = text.substring(0, 100);
    document.getElementById('editPreview').classList.add('active');
    var input = document.getElementById('messageInput');
    input.value = text;
    input.focus();

    // Close context menu
    document.getElementById('contextMenu').classList.remove('active');
}

function cancelEdit() {
    state.editingMessage = null;
    document.getElementById('editPreview').classList.remove('active');
}

function deleteMessage(messageId) {
    if (!confirm('Tem certeza que deseja excluir esta mensagem?')) return;
    fetch('/api/messages/' + messageId, { 
        method: 'DELETE',
        headers: { 'X-CSRF-Token': csrfToken }
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) showToast(data.error, 'error');
        });
    document.getElementById('contextMenu').classList.remove('active');
}

function toggleReaction(messageId, emoji) {
    fetch('/api/messages/' + messageId + '/react', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken
        },
        body: JSON.stringify({ emoji: emoji })
    });
}

function pinMessage(messageId) {
    fetch('/api/messages/' + messageId + '/pin', { 
        method: 'POST',
        headers: { 'X-CSRF-Token': csrfToken }
    })
        .then(function(r) { return r.json(); });
    document.getElementById('contextMenu').classList.remove('active');
}

// Context menu
function showContextMenu(e, msg) {
    state.contextMessageId = msg.id;
    state.contextMessageData = msg;
    var menu = document.getElementById('contextMenu');
    var isOwn = msg.username === APP_DATA.username;

    document.getElementById('ctxEditBtn').style.display = isOwn ? 'flex' : 'none';
    document.getElementById('ctxDeleteBtn').style.display = (isOwn || APP_DATA.isAdmin) ? 'flex' : 'none';
    document.getElementById('ctxEditDivider').style.display = (isOwn || APP_DATA.isAdmin) ? 'block' : 'none';

    menu.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
    menu.style.top = Math.min(e.clientY, window.innerHeight - 200) + 'px';
    menu.classList.add('active');
}

function handleContextAction(action) {
    var id = state.contextMessageId;
    switch (action) {
        case 'reply': startReply(id); break;
        case 'react': openReactionPicker(id); break;
        case 'edit': startEdit(id); break;
        case 'delete': deleteMessage(id); break;
        case 'pin': pinMessage(id); break;
    }
    document.getElementById('contextMenu').classList.remove('active');
}

// ===================================================================
// FILE UPLOAD
// ===================================================================

function handleFileSelect(event) {
    var file = event.target.files[0];
    if (!file) return;

    if (file.size > 25 * 1024 * 1024) {
        showToast('Arquivo excede o limite de 25MB', 'error');
        return;
    }

    var formData = new FormData();
    formData.append('file', file);
    formData.append('room_id', state.currentRoomId);
    formData.append('csrf_token', csrfToken);

    // Show progress
    document.getElementById('uploadProgress').classList.add('active');
    document.getElementById('uploadProgressFill').style.width = '0%';
    document.getElementById('uploadProgressText').textContent = 'Enviando ' + file.name + '...';

    var xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', function(e) {
        if (e.lengthComputable) {
            var pct = Math.round((e.loaded / e.total) * 100);
            document.getElementById('uploadProgressFill').style.width = pct + '%';
            document.getElementById('uploadProgressText').textContent = 'Enviando... ' + pct + '%';
        }
    });

    xhr.addEventListener('load', function() {
        document.getElementById('uploadProgress').classList.remove('active');
        if (xhr.status === 200) {
            var result = JSON.parse(xhr.responseText);
            // Send file as message
            var msgData = {
                room_id: state.currentRoomId,
                content: '',
                message_type: 'file',
                file_url: result.url,
                file_name: result.original_name,
                file_type: result.type,
                file_size: result.size,
                reply_to_id: state.replyTo ? state.replyTo.id : null
            };
            state.socket.emit('room_message', msgData);

            // Local append
            var localMsg = {
                id: Date.now(),
                content: '',
                username: APP_DATA.username,
                user_id: APP_DATA.userId,
                room_id: state.currentRoomId,
                timestamp: new Date().toISOString(),
                message_type: 'file',
                file_url: result.url,
                file_name: result.original_name,
                file_type: result.type,
                file_size: result.size,
                is_deleted: false,
                is_pinned: false,
                edited_at: null,
                reply_to: state.replyTo ? { id: state.replyTo.id, content: state.replyTo.content, username: state.replyTo.username } : null,
                reactions: [],
                avatar_color: '#d67f9d'
            };
            appendMessage(localMsg);
            scrollToBottom();
            cancelReply();

            showToast('Arquivo enviado!', 'success');
        } else {
            var err = JSON.parse(xhr.responseText);
            showToast(err.error || 'Erro no upload', 'error');
        }
    });

    xhr.addEventListener('error', function() {
        document.getElementById('uploadProgress').classList.remove('active');
        showToast('Erro ao enviar arquivo', 'error');
    });

    xhr.open('POST', '/api/upload');
    xhr.send(formData);

    // Reset file input
    event.target.value = '';
}

// ===================================================================
// EMOJI PICKER
// ===================================================================

function initEmojiPicker() {
    var grid = document.getElementById('emojiGrid');
    EMOJIS.forEach(function(emoji) {
        var btn = document.createElement('button');
        btn.className = 'emoji-item';
        btn.textContent = emoji;
        btn.onclick = function() {
            var input = document.getElementById('messageInput');
            input.value += emoji;
            input.focus();
            document.getElementById('emojiPicker').classList.remove('active');
        };
        grid.appendChild(btn);
    });

    // Reaction picker grid
    var rGrid = document.getElementById('reactionGrid');
    EMOJIS.forEach(function(emoji) {
        var btn = document.createElement('button');
        btn.className = 'emoji-item';
        btn.textContent = emoji;
        btn.onclick = function() {
            if (state.contextMessageId) {
                toggleReaction(state.contextMessageId, emoji);
            }
            closeModal('reactionPickerModal');
        };
        rGrid.appendChild(btn);
    });
}

function toggleEmojiPicker() {
    document.getElementById('emojiPicker').classList.toggle('active');
}

function openReactionPicker(messageId) {
    state.contextMessageId = messageId;
    openModal('reactionPickerModal');
}

// ===================================================================
// SEARCH
// ===================================================================

function performGlobalSearch(query) {
    fetch('/api/search?q=' + encodeURIComponent(query))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            // Show results as filtered room list
            if (data.rooms.length > 0) {
                state.rooms = data.rooms.concat(
                    state.rooms.filter(function(r) {
                        return !data.rooms.some(function(sr) { return sr.id === r.id; });
                    })
                );
            }
            renderRoomList();
        });
}

function toggleRoomSearch() {
    document.getElementById('searchResultsPanel').classList.toggle('active');
    var input = document.getElementById('roomSearchInput');
    if (document.getElementById('searchResultsPanel').classList.contains('active')) {
        input.focus();
    }
}

function closeSearchResults() {
    document.getElementById('searchResultsPanel').classList.remove('active');
}

function searchInRoom(query) {
    if (!state.currentRoomId) return;
    fetch('/api/search?q=' + encodeURIComponent(query))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var list = document.getElementById('searchResultsList');
            list.innerHTML = '';
            var roomMessages = data.messages.filter(function(m) { return m.room_id === state.currentRoomId; });
            if (roomMessages.length === 0) {
                list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);">Nenhum resultado</div>';
                return;
            }
            roomMessages.forEach(function(msg) {
                var item = document.createElement('div');
                item.className = 'search-result-item';
                item.innerHTML =
                    '<div class="search-result-meta">' + escapeHtml(msg.username) + ' · ' + formatTime(msg.timestamp) + '</div>' +
                    '<div class="search-result-content">' + highlightText(msg.content, query) + '</div>';
                item.onclick = function() {
                    scrollToMessage(msg.id);
                    closeSearchResults();
                };
                list.appendChild(item);
            });
        });
}

function highlightText(text, query) {
    var escaped = escapeHtml(text);
    var re = new RegExp('(' + escapeRegex(query) + ')', 'gi');
    return escaped.replace(re, '<mark>$1</mark>');
}

// ===================================================================
// TYPING INDICATOR
// ===================================================================

function handleTypingIndicator(data) {
    var indicator = document.getElementById('typingIndicator');
    if (data.is_typing) {
        state.typingUsers[data.username] = Date.now();
    } else {
        delete state.typingUsers[data.username];
    }

    var typingList = Object.keys(state.typingUsers);
    if (typingList.length === 0) {
        indicator.innerHTML = '';
    } else if (typingList.length === 1) {
        indicator.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div> ' + escapeHtml(typingList[0]) + ' está digitando...';
    } else {
        indicator.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div> ' + typingList.length + ' pessoas estão digitando...';
    }
}

// ===================================================================
// ONLINE STATUS
// ===================================================================

function loadUsers() {
    fetch('/api/users')
        .then(function(r) { return r.json(); })
        .then(function(users) {
            state.allUsers = users;
        });
}

function updateUserOnlineStatus(data) {
    for (var i = 0; i < state.allUsers.length; i++) {
        if (state.allUsers[i].id === data.user_id) {
            state.allUsers[i].is_online = data.is_online;
            break;
        }
    }
    // Update room header if relevant
    if (state.currentRoomId) {
        loadRoomData(state.currentRoomId);
    }
}

function populateMemberSelect() {
    var list = document.getElementById('memberSelectList');
    list.innerHTML = '';
    state.allUsers.forEach(function(u) {
        if (u.id === APP_DATA.userId) return;
        var item = document.createElement('label');
        item.className = 'user-select-item';
        item.innerHTML = '<input type="checkbox" value="' + u.id + '">' +
            '<div class="user-avatar" style="background:' + u.avatar_color + ';width:28px;height:28px;font-size:0.7rem;">' + u.username.charAt(0).toUpperCase() + '</div>' +
            '<span>' + escapeHtml(u.username) + '</span>';
        list.appendChild(item);
    });
}

// ===================================================================
// NOTIFICATIONS
// ===================================================================

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission !== 'granted') {
        Notification.requestPermission();
    }
}

function sendNotificationIfNeeded(data) {
    if (data.username === APP_DATA.username) return;
    if (document.hasFocus()) return;

    if (Notification.permission === 'granted') {
        new Notification(data.username, {
            body: data.content || '📎 Arquivo enviado',
            icon: '/static/icons/icon-192x192.png',
            tag: 'msg-' + data.room_id
        });
    }
}

// Push subscription
if ('serviceWorker' in navigator && 'PushManager' in window) {
    navigator.serviceWorker.ready.then(function(swReg) {
        swReg.pushManager.getSubscription().then(function(sub) {
            if (!sub) {
                Notification.requestPermission().then(function(perm) {
                    if (perm === 'granted') subscribeUser(swReg);
                });
            }
        });
    });
}

function subscribeUser(swReg) {
    var key = urlB64ToUint8Array('BKGeyfjwHzKcgPEM0I-XqudWHWiSVuOIFcBs5dLv5hOy9BhAaFbznVbsHqqi8zXzHcHefAMa0qpIuDVI4vAMKvI');
    swReg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })
        .then(function(sub) {
            fetch('/subscribe', {
                method: 'POST',
                body: JSON.stringify(sub),
                headers: { 'Content-Type': 'application/json' }
            });
        });
}

function urlB64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - base64String.length % 4) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var rawData = window.atob(base64);
    var arr = new Uint8Array(rawData.length);
    for (var i = 0; i < rawData.length; ++i) arr[i] = rawData.charCodeAt(i);
    return arr;
}

// ===================================================================
// LIGHTBOX
// ===================================================================

function openLightbox(src) {
    document.getElementById('lightboxImg').src = src;
    document.getElementById('lightbox').classList.add('active');
}

function closeLightbox() {
    document.getElementById('lightbox').classList.remove('active');
}

// ===================================================================
// UTILITIES
// ===================================================================

function scrollToBottom() {
    var el = document.getElementById('messages');
    setTimeout(function() {
        el.scrollTop = el.scrollHeight;
    }, 50);
}

function scrollToMessage(msgId) {
    var el = document.querySelector('[data-message-id="' + msgId + '"]');
    if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.style.animation = 'none';
        el.offsetHeight;
        el.style.animation = 'msgSlideIn 0.5s ease';
    }
}

function escapeHtml(text) {
    if (!text) return '';
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function formatTime(isoString) {
    if (!isoString) return '';
    var date = new Date(isoString);
    return date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function formatTimeShort(isoString) {
    if (!isoString) return '';
    var date = new Date(isoString);
    var now = new Date();
    var diff = now - date;

    if (diff < 86400000 && date.getDate() === now.getDate()) {
        return date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    }
    if (diff < 172800000) return 'Ontem';
    if (diff < 604800000) {
        return date.toLocaleDateString('pt-BR', { weekday: 'short' });
    }
    return date.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

function formatDate(isoString) {
    if (!isoString) return '';
    var date = new Date(isoString);
    var now = new Date();
    if (date.toDateString() === now.toDateString()) return 'Hoje';
    var yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return 'Ontem';
    return date.toLocaleDateString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}