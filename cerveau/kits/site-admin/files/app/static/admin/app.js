// Lignes de document : ajout et retrait. Confirmation avant suppression. Menu mobile. Aperçu des images choisies.
document.addEventListener("click", (e) => {
  const ajouter = e.target.closest("[data-ajouter]");
  if (ajouter) {
    const nom = ajouter.dataset.ajouter;
    const modele = document.querySelector(`template[data-modele="${nom}"]`);
    const corps = document.querySelector(`table[data-lignes="${nom}"] tbody`);
    const indices = [...document.querySelectorAll(`[name^="${nom}-"]`)]
      .map((el) => parseInt(el.name.split("-")[1], 10)).filter((n) => !isNaN(n));
    const suivant = indices.length ? Math.max(...indices) + 1 : 0;
    corps.insertAdjacentHTML("beforeend", modele.innerHTML.replaceAll("__i__", suivant));
    corps.lastElementChild.querySelector("input, select")?.focus();
  }
  if (e.target.closest("[data-retirer]")) e.target.closest("tr").remove();
  const menu = e.target.closest("[data-menu]");
  if (menu) {
    const ouvert = menu.closest(".menu").classList.toggle("ouvert");
    menu.setAttribute("aria-expanded", ouvert ? "true" : "false");
  }
});
document.addEventListener("submit", (e) => {
  const message = e.target.dataset.confirmer;
  if (message && !confirm(message)) e.preventDefault();
});
document.addEventListener("change", (e) => {
  const champ = e.target;
  if (champ.type !== "file" || !champ.files || !champ.files[0]) return;
  const apercu = document.querySelector(`img[data-apercu-de="${champ.name}"]`);
  if (!apercu) return;
  apercu.src = URL.createObjectURL(champ.files[0]);
  apercu.alt = "Nouvelle image (enregistrer pour la garder)";
  apercu.hidden = false;
});
