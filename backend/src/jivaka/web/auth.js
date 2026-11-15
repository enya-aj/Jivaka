// Shared by every signed-in page: sends the browser back to /login when the
// session has ended, and shows who is signed in (in #userbar, if present).
(function () {
  const realFetch = window.fetch.bind(window);
  window.fetch = async function (...args) {
    const res = await realFetch(...args);
    if (res.status === 401) {
      window.location.href = "/login";
    }
    return res;
  };

  const ROLE_LABELS = { admin: "Admin", doctor: "Doctor", student: "Student" };

  window.jivakaUser = realFetch("/me").then((res) => {
    if (res.status === 401) {
      window.location.href = "/login";
      return null;
    }
    return res.ok ? res.json() : null;
  });

  function render(user) {
    const bar = document.getElementById("userbar");
    if (!bar || !user) return;
    bar.textContent = "";
    const who = document.createElement("span");
    who.className = "userbar-who";
    who.textContent = `${user.name || user.email} · ${ROLE_LABELS[user.role] || user.role}`;
    who.title = user.email;
    bar.appendChild(who);
    if (user.role === "admin" && window.location.pathname !== "/admin") {
      const admin = document.createElement("a");
      admin.href = "/admin";
      admin.textContent = "Users";
      bar.appendChild(admin);
    }
    if (user.auth_enabled) {
      const form = document.createElement("form");
      form.method = "post";
      form.action = "/auth/logout";
      form.style.display = "inline";
      const button = document.createElement("button");
      button.type = "submit";
      button.className = "userbar-signout";
      button.textContent = "Sign out";
      form.appendChild(button);
      bar.appendChild(form);
    }
    document.querySelectorAll("[data-needs-ingest]").forEach((el) => {
      el.hidden = !user.can_ingest;
    });
  }

  const style = document.createElement("style");
  style.textContent = `
    #userbar { display: flex; gap: 0.75rem; align-items: center; font-size: 0.85rem; color: var(--muted, #6b7280); }
    #userbar a { color: var(--accent, #2563eb); }
    .userbar-signout { background: none; border: 1px solid var(--border, #d8dde3); color: inherit;
      border-radius: 6px; padding: 0.15rem 0.6rem; font: inherit; cursor: pointer; }
  `;
  document.head.appendChild(style);

  window.jivakaUser.then((user) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => render(user));
    } else {
      render(user);
    }
  });
})();
