/* Menu mobile et animations d'apparition.
   Règles du site :
   - les animations se déclenchent seules quand un élément arrive à l'écran ;
   - jamais de section épinglée qui bloque le défilement (aucune option d'épinglage ScrollTrigger) ;
   - aucune animation si le visiteur a demandé « réduire les animations » ;
   - sans GSAP (CDN bloqué), tout reste visible.
   Pour animer un élément : lui ajouter l'attribut data-anim dans la page. */
(function () {
  "use strict";

  var racine = document.documentElement;

  /* --- Menu mobile ------------------------------------------------------- */
  var bouton = document.querySelector(".nav-bouton");
  var nav = document.getElementById("navigation");

  function fermerMenu() {
    if (!bouton || !nav) return;
    bouton.setAttribute("aria-expanded", "false");
    nav.classList.remove("ouverte");
  }

  if (bouton && nav) {
    bouton.addEventListener("click", function () {
      var ouvert = bouton.getAttribute("aria-expanded") === "true";
      bouton.setAttribute("aria-expanded", ouvert ? "false" : "true");
      nav.classList.toggle("ouverte", !ouvert);
    });
    nav.addEventListener("click", function (e) {
      if (e.target.closest("a")) fermerMenu();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") fermerMenu();
    });
  }

  /* --- Animations -------------------------------------------------------- */
  function toutAfficher() {
    racine.classList.remove("js");
  }

  var mouvementReduit = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (mouvementReduit || !racine.classList.contains("js") || !window.gsap || !window.ScrollTrigger) {
    toutAfficher();
    return;
  }

  try {
    var gsap = window.gsap;
    gsap.registerPlugin(window.ScrollTrigger);

    var elements = gsap.utils.toArray("[data-anim]");

    function reveler(lot) {
      lot.forEach(function (el) { el.classList.add("vu"); });
      gsap.to(lot, { opacity: 1, y: 0, duration: 0.8, ease: "power3.out", stagger: 0.09, overwrite: true });
    }

    // Apparition par groupes à l'arrivée dans l'écran, une seule fois.
    window.ScrollTrigger.batch(elements, { start: "top 92%", once: true, onEnter: reveler });

    // Filet de sécurité : arrivé en bas de page, tout ce qui reste masqué apparaît.
    window.ScrollTrigger.create({
      trigger: document.body,
      start: "bottom bottom",
      once: true,
      onEnter: function () {
        var restants = elements.filter(function (el) { return !el.classList.contains("vu"); });
        if (restants.length) reveler(restants);
      }
    });

    window.animationsPretes = true;
  } catch (erreur) {
    toutAfficher();
    if (window.console) console.error(erreur);
  }
})();
