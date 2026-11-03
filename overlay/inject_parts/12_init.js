  createCenterCrosshair();
  ensureStatusToggleBtn();
  updatePanel();
  ensureWpToggleBtn();
  ensureCenterTeleportBtn();
  ensureCenterTeleportPanel();
  applyRoundLayout(!!(window.__cdSettings && window.__cdSettings.roundWindow));
  // ambos os painéis começam ocultos
  ensureWaypointPanel();
  renderWaypoints();
  updateTeleportVisibility();
  connect();
  setInterval(() => {
    if (window.mapManager && window.mapManager.map && window.mapManager.map.isStyleLoaded() && typeof window.mapManager.updateFoundLocationsStyle === 'function')
      window.mapManager.updateFoundLocationsStyle();
  }, 50);
})();
