// Checks for new alerts once a minute while any dashboard page is open. New alerts update
// the header badge, appear as a banner and, if allowed, as a desktop notification.
(function () {
  const CHECK_EVERY_MS = 60 * 1000;
  const BANNER_MS = 20 * 1000;
  const body = document.body;
  const badge = document.getElementById("alert-count");
  const banners = document.getElementById("alert-banners");
  const coins = new Intl.NumberFormat("en-GB");
  let latestId = Number(body.dataset.latestAlert || 0);

  function setUnread(count) {
    badge.querySelector("[data-count]").textContent = count;
    badge.hidden = count === 0;
  }

  function describe(alert) {
    return coins.format(alert.margin) + " gp margin, " +
      coins.format(alert.potentialProfit) + " gp potential profit, confidence " + alert.confidence;
  }

  function showBanner(alert) {
    const banner = document.createElement("p");
    banner.className = "notice banner";
    const link = document.createElement("a");
    link.href = alert.url;
    link.textContent = alert.itemName;
    banner.append("New flip: ", link, ". " + describe(alert) + ".");
    banners.append(banner);
    setTimeout(function () { banner.remove(); }, BANNER_MS);
  }

  function notify(alert) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    // The tag stops two open tabs showing the same alert twice.
    const notification = new Notification("New flip: " + alert.itemName, {
      body: describe(alert),
      tag: "flipwatch-alert-" + alert.id,
    });
    notification.onclick = function () { window.focus(); window.location = alert.url; };
  }

  async function check() {
    try {
      const response = await fetch(body.dataset.alertCheck + "?since=" + latestId, { method: "POST" });
      const result = await response.json();
      result.alerts.forEach(function (alert) { showBanner(alert); notify(alert); });
      latestId = result.latestId;
      setUnread(result.unread);
    } catch (error) {
      // A failed check is retried at the next interval, so there is nothing to show.
    }
  }

  const enable = document.getElementById("enable-notifications");
  if (enable && "Notification" in window && Notification.permission === "default") {
    enable.hidden = false;
    enable.addEventListener("click", async function () {
      await Notification.requestPermission();
      enable.hidden = true;
    });
  }

  check();
  setInterval(check, CHECK_EVERY_MS);
})();
