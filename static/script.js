const chatBox = document.getElementById("chatBox");
const userInput = document.getElementById("userInput");
const btnSend = document.getElementById("btnSend");
const btnClear = document.getElementById("btnClear");
const btnFullscreen = document.getElementById("btnFullscreen");
const chatShell = document.getElementById("chatShell");
const btnToggleSidebar = document.getElementById("btnToggleSidebar");
const sidebar = document.getElementById("sidebar");
const historyList = document.getElementById("historyList");
const btnNewChat = document.getElementById("btnNewChat");
const btnTheme = document.getElementById("btnTheme");
const btnVoice = document.getElementById("btnVoice");

const HELP_TO_EMAIL = "zohaib190303@gmail.com";
const STORAGE_KEY = "uaf_cs_assistant_chats_v3";

let chats = [];
let currentChatId = null;

// =========================
// INITIALIZATION
// =========================
(function initApp() {
  showHomePage();
  loadChatsFromStorage();
  sortChatsByRecent();
  ensureValidCurrentChat();

  if (!chats.length) {
    createChat({ title: "New Chat", withWelcome: true });
  } else {
    renderHistory();
    renderCurrentChat();
  }
})();

// =========================
// HELPERS
// =========================
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[m]));
}

function getWelcomeMessage() {
  return "Hello! I am the Smart CS Department Assistant for UAF.\n\n" +
    "You can ask me about:\n• CS faculty information\n• Dean / VC details\n" +
    "• Office floors and contact numbers\n• Fee structure\n• UAF events and notices\n" +
    "• Admissions and merit lists\n• or Ask anything";
}

function generateChatId() {
  return "chat_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
}

function nowIso() { return new Date().toISOString(); }

function saveChats() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ chats, currentChatId }));
}

function loadChatsFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    chats = parsed.chats || [];
    currentChatId = parsed.currentChatId;
  } catch {
    chats = [];
    currentChatId = null;
  }
}

function sortChatsByRecent() {
  chats.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

function ensureValidCurrentChat() {
  if (!chats.find(c => c.id === currentChatId)) {
    currentChatId = chats[0]?.id || null;
  }
}

function getCurrentChat() {
  return chats.find(c => c.id === currentChatId);
}

// =========================
// UI CONTROLS
// =========================
function toggleTyping(show) {
  const existing = document.getElementById("typingIndicator");
  if (show) {
    if (existing) return;
    const row = document.createElement("div");
    row.className = "msg assistant";
    row.id = "typingIndicator";
    row.innerHTML = `<div class="bubble"><div class="typing"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div></div>`;
    chatBox.appendChild(row);
    chatBox.scrollTop = chatBox.scrollHeight;
  } else if (existing) {
    existing.remove();
  }
}

function toggleSidebar() {
  sidebar.classList.toggle("hidden");
}

function showHomePage() {
  window.scrollTo({ top: 0, behavior: "instant" });
}

// =========================
// CHAT LOGIC
// =========================
function createChat({ title = "New Chat", withWelcome = true } = {}) {
  const chat = {
    id: generateChatId(),
    title,
    createdAt: nowIso(),
    updatedAt: nowIso(),
    messages: []
  };

  if (withWelcome) {
    chat.messages.push({ role: "assistant", content: getWelcomeMessage(), source: "Ready" });
  }

  chats.unshift(chat);
  currentChatId = chat.id;
  saveChats();
  renderHistory();
  renderCurrentChat();
}

function pushMessage(role, content, source = "") {
  let chat = getCurrentChat();
  if (!chat) {
    createChat({ withWelcome: false });
    chat = getCurrentChat();
  }
  chat.messages.push({ role, content, source });
  if (role === "user" && chat.title === "New Chat") chat.title = content.slice(0, 40);
  chat.updatedAt = nowIso();
  saveChats();
  renderHistory();
}

function addMessage(text, role = "assistant", source = "") {
  const row = document.createElement("div");
  row.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br/>");
  if (source) bubble.innerHTML += `<div class="meta"><span class="badge-src">${source}</span></div>`;
  row.appendChild(bubble);
  chatBox.appendChild(row);
  chatBox.scrollTop = chatBox.scrollHeight;
}

async function sendQuery() {
  const q = userInput.value.trim();
  if (!q) return;

  addMessage(q, "user");
  pushMessage("user", q);
  userInput.value = "";

  toggleTyping(true);

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q })
    });
    const data = await res.json();

    toggleTyping(false);
    addMessage(data.answer, "assistant", data.source);
    pushMessage("assistant", data.answer, data.source);
  } catch (error) {
    toggleTyping(false);
    addMessage("Server error. Please check if the backend or Ollama is running.", "assistant");
  }
}

// =========================
// RENDERING
// =========================
function renderCurrentChat() {
  chatBox.innerHTML = "";
  const chat = getCurrentChat();
  if (chat) chat.messages.forEach(m => addMessage(m.content, m.role, m.source));
}

function renderHistory() {
  historyList.innerHTML = "";
  chats.forEach(chat => {
    const item = document.createElement("div");
    item.className = "history-item" + (chat.id === currentChatId ? " active" : "");
    item.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;width:100%;overflow:hidden;">
        <div class="chat-title" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;margin-right:8px;">${escapeHtml(chat.title)}</div>
        <div class="actions" style="flex-shrink:0;">
          <button class="rename-btn" title="Rename">✏️</button>
          <button class="delete-btn" title="Delete">🗑</button>
        </div>
      </div>`;

    item.onclick = (e) => {
      if (e.target.closest('.rename-btn')) {
        const name = prompt("Rename chat:", chat.title);
        if (name) { chat.title = name; saveChats(); renderHistory(); }
        return;
      }
      
      if (e.target.closest('.delete-btn')) {
        if (confirm("Delete chat?")) {
          chats = chats.filter(c => c.id !== chat.id);
          currentChatId = chats[0]?.id || null;
          saveChats(); renderHistory(); renderCurrentChat();
        }
        return;
      }

      currentChatId = chat.id;
      saveChats();
      renderCurrentChat();
      renderHistory();
    };

    historyList.appendChild(item);
  });
}

const btnSendHelp = document.getElementById("btnSendHelp");
const helpName = document.getElementById("helpName");
const helpEmail = document.getElementById("helpEmail");
const helpMsg = document.getElementById("helpMsg");

// =========================
// EVENT LISTENERS
// =========================
btnSend?.addEventListener("click", sendQuery);
userInput?.addEventListener("keydown", e => { if (e.key === "Enter") sendQuery(); });
btnNewChat?.addEventListener("click", () => createChat({ withWelcome: true }));
btnToggleSidebar?.addEventListener("click", toggleSidebar);
btnFullscreen?.addEventListener("click", () => {
  if (!document.fullscreenElement) chatShell.requestFullscreen();
  else document.exitFullscreen();
});

btnSendHelp?.addEventListener("click", async () => {
  const name = helpName?.value.trim() || "User";
  const email = helpEmail?.value.trim() || "Not provided";
  const msg = helpMsg?.value.trim();

  if (!msg) {
    alert("Please enter a message before sending.");
    return;
  }

  const originalText = btnSendHelp.textContent;
  btnSendHelp.textContent = "Sending...";
  btnSendHelp.disabled = true;

  try {
    const res = await fetch("/api/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, message: msg })
    });
    
    const data = await res.json();
    
    if (res.ok) {
      alert("✅ Your email was sent successfully!");
      if (helpMsg) helpMsg.value = "";
    } else {
      alert("❌ Error: " + (data.error || "Could not send email."));
    }
  } catch (err) {
    alert("❌ Failed to connect to the server.");
  } finally {
    btnSendHelp.textContent = originalText;
    btnSendHelp.disabled = false;
  }
});

btnClear?.addEventListener("click", () => {
  const chat = getCurrentChat();
  if (chat) { chat.messages = []; saveChats(); renderCurrentChat(); }
});

btnTheme?.addEventListener("click", () => {
  const curr = document.documentElement.getAttribute("data-theme") || "dark";
  const next = curr === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
});

// Init Theme
document.documentElement.setAttribute("data-theme", localStorage.getItem("theme") || "dark");

// Find your btnFullscreen listener and update it to this:
btnFullscreen?.addEventListener("click", () => {
  if (!document.fullscreenElement) {
    chatShell.requestFullscreen();
    chatShell.classList.add("fullscreen"); // Force the class
    document.body.classList.add("fullscreen-active");
  } else {
    document.exitFullscreen();
    chatShell.classList.remove("fullscreen");
    document.body.classList.remove("fullscreen-active");
  }
});

// Also add this to handle the "Esc" key properly:
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement) {
    chatShell.classList.remove("fullscreen");
    document.body.classList.remove("fullscreen-active");
  }
});

// Voice Setup
let recognition;
function initSpeech() {
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) return null;
  const r = new Speech();
  r.lang = "en-US";
  r.onstart = () => btnVoice.textContent = "🛑";
  r.onend = () => btnVoice.textContent = "🎤";
  r.onresult = (e) => { userInput.value = e.results[0][0].transcript; sendQuery(); };
  return r;
}
recognition = initSpeech();
btnVoice?.addEventListener("click", () => {
  if (!recognition) return alert("Use Chrome for Voice");
  btnVoice.textContent === "🎤" ? recognition.start() : recognition.stop();
});

