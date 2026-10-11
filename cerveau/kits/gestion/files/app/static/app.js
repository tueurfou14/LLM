// Lignes de document : ajout et retrait. Confirmation avant suppression. Menu mobile.
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
  if (e.target.closest("[data-menu]")) e.target.closest(".menu").classList.toggle("ouvert");
});
document.addEventListener("submit", (e) => {
  const message = e.target.dataset.confirmer;
  if (message && !confirm(message)) e.preventDefault();
});
