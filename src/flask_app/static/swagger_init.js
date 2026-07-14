(() => {
  const script = document.currentScript;
  const specPath = script.dataset.specPath || "/openapi.json";
  window.addEventListener("load", () => {
    window.ui = window.SwaggerUIBundle({
      url: specPath,
      dom_id: "#swagger-ui",
      deepLinking: true,
      displayRequestDuration: true,
      persistAuthorization: true,
      tryItOutEnabled: false
    });
  });
})();
