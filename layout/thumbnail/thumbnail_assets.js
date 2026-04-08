/**
 * Thumbnail-specific asset utilities.
 * Extracted from globals.js and assetUtils.js for static (non-live) rendering.
 * No SocketIO, no GSAP animations, no polling.
 */

// --- Math ---

const degrees_to_radians = (deg) => (deg * Math.PI) / 180.0;

// --- Asset selection ---

function GetBiggestAsset(character) {
  let biggest = null;
  if (Object.entries(character.assets).length > 0) {
    biggest = Object.entries(character.assets)[0];
    if (Object.entries(character.assets).length > 1) {
      for (let i = 1; i < Object.keys(character.assets).length; i += 1) {
        try {
          let currSum = biggest[1].average_size.x + biggest[1].average_size.y;
          let newSum =
            Object.entries(character.assets)[i][1].average_size.x +
            Object.entries(character.assets)[i][1].average_size.y;
          if (newSum > currSum) {
            biggest = Object.entries(character.assets)[i];
          }
        } catch (e) {}
      }
    }
  }
  if (biggest) return biggest[1];
  else return null;
}

function GetCharacterAsset(assetKey, character) {
  if (Array.isArray(assetKey)) {
    for (let i = 0; i < assetKey.length; i++) {
      if (character.assets.hasOwnProperty(assetKey[i])) {
        return character.assets[assetKey[i]];
      }
    }
    return GetBiggestAsset(character);
  } else {
    if (character.assets.hasOwnProperty(assetKey)) {
      return character.assets[assetKey];
    } else {
      return GetBiggestAsset(character);
    }
  }
}

function GetRecommendedZoom(asset, width, height) {
  if (asset.uncropped_edge) {
    if (
      asset.uncropped_edge.includes("u") &&
      asset.uncropped_edge.includes("d") &&
      asset.uncropped_edge.includes("l") &&
      asset.uncropped_edge.includes("r")
    ) {
      return 1.2;
    } else if (
      ((asset.uncropped_edge.includes("l") &&
        !asset.uncropped_edge.includes("r")) ||
        (!asset.uncropped_edge.includes("l") &&
          asset.uncropped_edge.includes("r"))) &&
      width > height
    ) {
      return width / height;
    } else if (
      ((asset.uncropped_edge.includes("u") &&
        !asset.uncropped_edge.includes("d")) ||
        (!asset.uncropped_edge.includes("u") &&
          asset.uncropped_edge.includes("d"))) &&
      height > width
    ) {
      return height / width;
    }
  }
  return 1;
}

// --- Multi-character positioning ---

function GenerateMulticharacterPositions(
  character_number,
  center = [0.5, 0.5],
  radius = 0.3
) {
  let positions = [];
  if (character_number == 1) radius = 0;
  let angle_rad = degrees_to_radians(90);
  if (character_number == 2) angle_rad = degrees_to_radians(45);
  let pendulum = 1;

  for (let i = 0; i < character_number; i += 1) {
    let j = i;
    if (i > 1) {
      if (i % 2 == 0) {
        pendulum *= -1;
      } else {
        pendulum *= -1;
        pendulum += 1;
      }
      j = pendulum;
    }
    let angle = angle_rad + degrees_to_radians(360 / character_number) * j;
    let pos = [
      center[0] + Math.cos(angle) * radius,
      center[1] + Math.sin(angle) * radius,
    ];
    positions.push(pos);
  }
  return positions;
}

// --- Image centering (eyesight-based) ---

function resizeInCanvas(image, width, height) {
  if (width >= image.width || height >= image.height) {
    return image.src;
  }
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  canvas.width = width;
  canvas.height = height;
  ctx.imageSmoothingQuality = "medium";
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  const dataURI = canvas.toDataURL("image/webp");
  canvas.remove();
  return dataURI;
}

/**
 * Center a character image inside an element using eyesight-based positioning.
 * Returns a Promise that resolves when the image is loaded and positioned.
 */
async function CenterImage(element, assetData, options = {}) {
  options = _.defaultsDeep(options, {
    custom_zoom: 1,
    custom_center: [0.5, 0.5],
    scale_based_on_parent: false,
    scale_fill_x: false,
    scale_fill_y: false,
  });

  if (!assetData) return;
  options.asset_data = assetData;
  $(element).data(options);
  await CenterImageDo($(element));
}

async function CenterImageDo(element) {
  try {
    let data = $(element).data();
    let assetData = data.asset_data;
    let customZoom = data.custom_zoom;
    let customCenter = data.custom_center;
    let scale_based_on_parent = data.scale_based_on_parent;
    let scale_fill_x = data.scale_fill_x;
    let scale_fill_y = data.scale_fill_y;

    let customElement = null;
    if (scale_based_on_parent) {
      customElement = element.parent().parent();
    }

    if (typeof assetData == "string") {
      assetData = JSON.parse(assetData);
    }
    if (!assetData) return;

    let image = 'url("../../' + assetData.asset + '")';

    if (image != undefined && image.includes("url(")) {
      function loadImage() {
        return new Promise((resolve) => {
          const img = new Image();
          img.src = "../../" + assetData.asset;
          img.onload = async () => {
            let eyesight = assetData.eyesight;
            if (!eyesight) {
              eyesight = {
                x: img.naturalWidth / 2,
                y: img.naturalHeight / 2,
              };
            }

            if (!customElement) customElement = element;

            let proportional_zoom = 1;
            if (assetData.average_size) {
              proportional_zoom = 0;
              proportional_zoom = Math.max(
                proportional_zoom,
                ($(customElement).innerWidth() / assetData.average_size.x) * 1.2
              );
              proportional_zoom = Math.max(
                proportional_zoom,
                ($(customElement).innerHeight() / assetData.average_size.y) *
                  1.2
              );
            }

            let zoom_x = $(customElement).innerWidth() / img.naturalWidth;
            let zoom_y = $(customElement).innerHeight() / img.naturalHeight;
            let minZoom = 1;
            let rescalingFactor = 1;

            if (assetData.rescaling_factor)
              rescalingFactor = assetData.rescaling_factor;

            let uncropped_edge = assetData.uncropped_edge;

            if (
              !uncropped_edge ||
              uncropped_edge == "undefined" ||
              uncropped_edge.length == 0
            ) {
              if (zoom_x > zoom_y) {
                minZoom = zoom_x * rescalingFactor;
              } else {
                minZoom = zoom_y * rescalingFactor;
              }
            } else {
              if (
                uncropped_edge.includes("u") &&
                uncropped_edge.includes("d") &&
                uncropped_edge.includes("l") &&
                uncropped_edge.includes("r")
              ) {
                minZoom = customZoom * proportional_zoom * rescalingFactor;
              } else if (
                !uncropped_edge.includes("l") &&
                !uncropped_edge.includes("r")
              ) {
                minZoom = zoom_x * rescalingFactor;
              } else if (
                !uncropped_edge.includes("u") &&
                !uncropped_edge.includes("d")
              ) {
                minZoom = zoom_y * rescalingFactor;
              } else {
                minZoom = customZoom * proportional_zoom * rescalingFactor;
              }
            }

            if (scale_fill_x && !scale_fill_y) {
              minZoom = zoom_x;
            } else if (scale_fill_y && !scale_fill_x) {
              minZoom = zoom_y;
            } else if (scale_fill_x && scale_fill_y) {
              minZoom = Math.max(zoom_x, zoom_y);
            }

            let zoom = Math.max(minZoom, customZoom * minZoom);

            // Centering
            let xx = 0;
            let yy = 0;

            if (!customCenter) {
              xx = -eyesight.x * zoom + $(element).innerWidth() / 2;
            } else {
              xx =
                -eyesight.x * zoom + $(element).innerWidth() * customCenter[0];
            }

            let maxMoveX = $(element).innerWidth() - img.naturalWidth * zoom;
            if (!uncropped_edge || !uncropped_edge.includes("l")) {
              if (xx > 0) xx = 0;
            }
            if (!uncropped_edge || !uncropped_edge.includes("r")) {
              if (xx < maxMoveX) xx = maxMoveX;
            }

            if (!customCenter) {
              yy = -eyesight.y * zoom + $(element).innerHeight() / 2;
            } else {
              yy =
                -eyesight.y * zoom +
                $(element).innerHeight() * customCenter[1];
            }

            let maxMoveY = $(element).innerHeight() - img.naturalHeight * zoom;
            if (!uncropped_edge || !uncropped_edge.includes("u")) {
              if (yy > 0) yy = 0;
            }
            if (!uncropped_edge || !uncropped_edge.includes("d")) {
              if (yy < maxMoveY) yy = maxMoveY;
            }

            if (data.use_dividers === false) {
              $(element).parent().css("position", "absolute");
            }

            if (data.z_index) {
              $(element).parent().css("z-index", data.z_index);
            } else {
              $(element).parent().css("z-index", 0);
            }

            $(element).css({
              "background-position": `${xx}px ${yy}px`,
              "background-size": `${img.naturalWidth * zoom}px ${
                img.naturalHeight * zoom
              }px`,
              "background-repeat": "no-repeat",
              "background-image":
                "url(" +
                resizeInCanvas(
                  img,
                  img.naturalWidth * zoom,
                  img.naturalHeight * zoom
                ) +
                ")",
            });

            resolve();
          };
        });
      }

      await loadImage();
    }
  } catch (e) {
    console.log(e);
  }
}

// --- Text fitting ---

function FitText(target) {
  if (target == null) return;
  let textElement = target.find(".text");
  if (textElement.length === 0) return;

  textElement.css("transform", "");
  let scaleX = 1;
  if (textElement[0].scrollWidth * scaleX > target.width()) {
    scaleX = target.width() / textElement[0].scrollWidth;
    textElement.css("transform", "scaleX(" + scaleX + ")");
  }
}
