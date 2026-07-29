const statusTbody = document.getElementById("statusTbody");
const btnRefreshStatus = document.getElementById("btnRefreshStatus");
const btnOpenLogin = document.getElementById("btnOpenLogin");
const btnLogout = document.getElementById("btnLogout");

const loginModal = document.getElementById("loginModal");
const btnCloseLogin = document.getElementById("btnCloseLogin");
const btnDoLogin = document.getElementById("btnDoLogin");
const loginEmail = document.getElementById("loginEmail");
const loginPassword = document.getElementById("loginPassword");
const loginError = document.getElementById("loginError");

const statusToggleBar = document.getElementById("statusToggleBar");
const toggleIn = document.getElementById("toggleIn");
const toggleAway = document.getElementById("toggleAway");
const meLabel = document.getElementById("meLabel");

const btnTheme = document.getElementById("btnTheme");

let isLoggedIn = false;
let refreshTimer = null;

function escapeHtml(s){
  return String(s ?? "").replace(/[&<>"']/g, m => ({
    "&":"&amp;",
    "<":"&lt;",
    ">":"&gt;",
    '"':"&quot;",
    "'":"&#039;"
  }[m]));
}

function setTheme(theme){
  document.body.setAttribute("data-theme", theme);
  localStorage.setItem("theme", theme);
}

btnTheme?.addEventListener("click", () => {
  const curr = document.body.getAttribute("data-theme") || "dark";
  setTheme(curr === "dark" ? "light" : "dark");
});

(function initTheme(){
  const saved = localStorage.getItem("theme");
  setTheme(saved || "dark");
})();

function openLogin(){
  if (!loginModal) return;
  loginError.textContent = "";
  loginModal.classList.remove("hidden");
}

function closeLogin(){
  if (!loginModal) return;
  loginModal.classList.add("hidden");
}

function setToggleUI(status){
  if (!toggleIn || !toggleAway) return;
  toggleIn.classList.toggle("active", status === "in_office");
  toggleAway.classList.toggle("active", status === "away");
}

function setLoggedOutUI(){
  isLoggedIn = false;
  if (btnLogout) btnLogout.style.display = "none";
  if (statusToggleBar) statusToggleBar.classList.add("hidden");
  if (meLabel) meLabel.textContent = "";
  setToggleUI("unknown");
}

function setLoggedInUI(email, status){
  isLoggedIn = true;
  if (btnLogout) btnLogout.style.display = "inline-flex";
  if (statusToggleBar) statusToggleBar.classList.remove("hidden");
  if (meLabel) meLabel.textContent = `Logged in: ${email}`;
  setToggleUI(status || "unknown");
}

function renderStatus(items){
  const safeItems = (items || []).filter(t => {
    const name = String(t?.name || "").trim().toLowerCase();
    return name && name !== "unknown";
  });

  if (!safeItems.length){
    statusTbody.innerHTML = `<tr><td colspan="3" class="muted">No teachers found.</td></tr>`;
    return;
  }

  statusTbody.innerHTML = "";

  safeItems.forEach(t => {
    const s = String(t.status || "unknown").trim();
    const dot = s === "in_office" ? "dot-green" : s === "away" ? "dot-red" : "dot-gray";
    const label = s === "in_office" ? "In Office" : s === "away" ? "Away" : "Unknown";

    const tr = document.createElement("tr");
    tr.className = "status-row";
    tr.innerHTML = `
      <td>${escapeHtml(t.name || "")}</td>
      <td>
        <span class="avail-pill ${escapeHtml(s)}">
          <span class="${dot}"></span>
          ${label}
        </span>
      </td>
      <td class="muted">${escapeHtml(t.updated_at || "-")}</td>
    `;

    statusTbody.appendChild(tr);
  });
}

async function loadStatus(){
  try{
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error("Failed");
    const data = await res.json();
    renderStatus(data.items || []);
  } catch(e){
    statusTbody.innerHTML = `<tr><td colspan="3" class="muted">Failed to load status.</td></tr>`;
  }
}

async function checkSession(){
  try{
    const res = await fetch("/api/me", { cache: "no-store" });
    if (!res.ok) throw new Error("Failed");
    const data = await res.json();

    if (data.logged_in){
      setLoggedInUI(data.email, data.status || "unknown");
    } else {
      setLoggedOutUI();
    }
  } catch(e){
    setLoggedOutUI();
  }
}

async function doLogin(){
  loginError.textContent = "";

  const email = (loginEmail.value || "").trim();
  const password = (loginPassword.value || "").trim();

  if (!email || !password){
    loginError.textContent = "Email and password required.";
    return;
  }

  try{
    const res = await fetch("/api/login", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ email, password })
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok || !data.ok){
      loginError.textContent = data.error || "Login failed.";
      return;
    }

    closeLogin();
    loginEmail.value = "";
    loginPassword.value = "";

    await checkSession();
    await loadStatus();
  } catch(e){
    loginError.textContent = "Login failed.";
  }
}

async function setMyStatus(status){
  if (!isLoggedIn){
    openLogin();
    return;
  }

  try{
    const res = await fetch("/api/set_status", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ status })
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok){
      alert(data.error || "Failed to update status.");
      return;
    }

    setToggleUI(status);
    await checkSession();
    await loadStatus();
  } catch(e){
    alert("Failed to update status.");
  }
}

async function doLogout(){
  try{
    await fetch("/api/logout", { method:"POST" });
  } catch(e) {}
  setLoggedOutUI();
  await loadStatus();
}

function bindEvents(){
  btnRefreshStatus?.addEventListener("click", loadStatus);
  btnOpenLogin?.addEventListener("click", openLogin);
  btnCloseLogin?.addEventListener("click", closeLogin);
  btnDoLogin?.addEventListener("click", doLogin);

  toggleIn?.addEventListener("click", () => setMyStatus("in_office"));
  toggleAway?.addEventListener("click", () => setMyStatus("away"));

  btnLogout?.addEventListener("click", doLogout);

  loginPassword?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });

  loginModal?.addEventListener("click", (e) => {
    if (e.target === loginModal) closeLogin();
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await checkSession();
  await loadStatus();

  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(loadStatus, 20000);
});