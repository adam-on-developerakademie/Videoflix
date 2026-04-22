document.addEventListener("DOMContentLoaded", function () {
  var fileInput = document.getElementById("id_video_file");
  if (!fileInput) return;

  fileInput.addEventListener("change", function () {
    var file = fileInput.files[0];
    if (!file) return;

    var nameWithoutExt = file.name.replace(/\.[^/.]+$/, "");

    var titleInput = document.getElementById("id_title");
    if (titleInput && !titleInput.value) {
      titleInput.value = nameWithoutExt;
    }

    var categoryInput = document.getElementById("id_category");
    if (categoryInput && !categoryInput.value) {
      categoryInput.value = "Auto";
    }

    var descInput = document.getElementById("id_description");
    if (descInput && !descInput.value) {
      descInput.value = "No description";
    }
  });
});
