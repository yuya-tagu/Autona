# This script can be used to generate thumbnails using ./out/program_state.json and ./thumbnail_base
# Run as python ./src/generate_thumbnail in order to test it

import itertools
from dataclasses import dataclass, field
from math import cos, radians, sin
import random
import traceback
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from pathlib import Path
import json
import shutil
import datetime
import os
import re
from loguru import logger

from src.TSHGameAssetManager import TSHGameAssetManager
from src.Helpers.TSHLocaleHelper import TSHLocaleHelper
from src.Helpers.TSHDictHelper import *
from src.Helpers.TSHAltTextHelper import generate_youtube
from src.Helpers.TSHDirHelper import TSHResolve


@dataclass
class ThumbnailContext:
    """Holds all state for a single thumbnail generation pass (thread-safe)."""
    is_preview: bool = False
    ratio: tuple = (1.0, 1.0)
    template_data: dict = field(default_factory=dict)
    no_separator: int = 0
    flip_direction: bool = False
    smooth_scale: bool = True
    no_separator_angle: int = 45
    no_separator_distance: int = 30
    proportional_scaling: bool = True
    separator_color_code: str = "#888888"
    separator_width: int = 5
    crop_borders: list = field(default_factory=list)
    scale_fill_x: int = 0
    scale_fill_y: int = 0
    proportional_zoom: float = 1.0
    text_color: list = field(default_factory=list)
    sponsor_color: list = field(default_factory=list)
    font_1: dict = field(default_factory=dict)
    font_2: dict = field(default_factory=dict)


def color_code_to_tuple(color_code):
    raw_color_code = color_code.lstrip("#")
    red = int(raw_color_code[0:2], base=16)
    green = int(raw_color_code[2:4], base=16)
    blue = int(raw_color_code[4:6], base=16)
    color = (red, green, blue)
    return color


def find(element, json):
    keys = element.split('.')
    rv = json
    for key in keys:
        rv = rv[key]
    return rv


def calculate_new_dimensions(current_size, max_size):
    # Use -1 if you do not want to constrain in that dimension
    x_ratio = max_size[0]/current_size.width()
    y_ratio = max_size[1]/current_size.height()

    if max_size[0] < 0 and max_size[1] < 0:
        raise ValueError(
            msg=f"Size cannot be negative, given max size is {max_size}")

    if (x_ratio*current_size.height() > max_size[1]) or x_ratio < 0:
        new_x = y_ratio*current_size.width()
        new_y = max_size[1]
    else:
        new_x = max_size[0]
        new_y = x_ratio*current_size.height()
    return ((round(new_x), round(new_y)))


def resize_image_to_max_size(ctx, image: QPixmap, max_size, eyesight_coordinates=None, fill_x=True, fill_y=True, zoom=1):
    current_size = image.size()
    x_ratio = max_size[0]/current_size.width()
    y_ratio = max_size[1]/current_size.height()

    if max_size[0] < 0 or max_size[1] < 0:
        raise ValueError(
            msg=f"Size cannot be negative, given max size is {max_size}")

    resized_eyesight = None
    effective_zoom = zoom
    zoom_step = 0.01
    zoom_flag = False
    while not zoom_flag:
        if (x_ratio < y_ratio):
            new_x = y_ratio*current_size.width()*effective_zoom
            new_y = max_size[1]*effective_zoom
            if eyesight_coordinates:
                resized_eyesight = (
                    round(eyesight_coordinates[0]*y_ratio*effective_zoom), round(eyesight_coordinates[1]*y_ratio*effective_zoom))
        else:
            new_x = max_size[0]*effective_zoom
            new_y = x_ratio*current_size.height()*effective_zoom
            if eyesight_coordinates:
                resized_eyesight = (
                    round(eyesight_coordinates[0]*x_ratio*effective_zoom), round(eyesight_coordinates[1]*x_ratio*effective_zoom))
        if not (new_x < max_size[0] and ("left" in ctx.crop_borders) and ("right" in ctx.crop_borders)):
            if not (new_y < max_size[1] and ("top" in ctx.crop_borders) and ("bottom" in ctx.crop_borders)):
                zoom_flag = True
        if not zoom_flag:
            effective_zoom += zoom_step

    new_size = (round(new_x), round(new_y))
    new_image = image.scaled(
        new_size[0],
        new_size[1],
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )

    # crop
    if not resized_eyesight:
        left = round(-(max_size[0] - new_x)/2)
        top = round(-(max_size[1] - new_y)/2)
        right = round((max_size[0] + new_x)/2)
        bottom = round((max_size[1] + new_y)/2)
    else:
        left = round(resized_eyesight[0]-(max_size[0]/2))
        top = round(resized_eyesight[1]-(max_size[1]/2))
        right = round(resized_eyesight[0]+(max_size[0]/2))
        bottom = round(resized_eyesight[1]+(max_size[1]/2))
        if left < 0:
            left = 0
            right = max_size[0]
        if top < 0:
            top = 0
            bottom = max_size[1]
        if right > new_x:
            right = new_x
            left = new_x - max_size[0]
        if bottom > new_y:
            bottom = new_y
            top = new_y - max_size[1]
    if max_size[0] > new_x:
        left = round(-(max_size[0] - new_x)/2)
        right = round((max_size[0] + new_x)/2)
        if ("left" in ctx.crop_borders):
            left = 0
            right = max_size[0]
        if ("right" in ctx.crop_borders):
            right = max_size[0]
            left = new_x - max_size[0]
    if max_size[1] > new_y:
        top = round(-(max_size[1] - new_y)/2)
        bottom = round((max_size[1] + new_y)/2)
        if ("top" in ctx.crop_borders):
            top = 0
            bottom = max_size[1]
        if ("bottom" in ctx.crop_borders):
            bottom = max_size[1]
            top = new_y - max_size[1]

    new_image = create_composite_image(
        new_image, QSize(max_size[0], max_size[1]), (-left, -top))

    return (new_image)


def create_composite_image(image, size, coordinates):
    background = QPixmap(size)
    background.fill(QColor(0, 0, 0, 0))
    painter = QPainter(background)
    painter.drawPixmap(int(coordinates[0]), int(coordinates[1]), image)
    painter.end()
    return (background)


def generate_multicharacter_positions(character_number, center=[0.5, 0.5], radius=0.3, angle=45):
    positions = []

    # For 1 character, just center it
    if character_number == 1:
        radius = 0

    angle_rad = radians(angle+45)

    if character_number == 2:
        angle_rad = radians(angle)

    pendulum = 1

    for i in range(character_number):
        if i > 1:
            if i % 2 == 0:
                pendulum *= -1
            else:
                pendulum *= -1
                pendulum += 1
            i = pendulum

        angle = angle_rad + radians(360/character_number) * i
        pos = [
            center[0] + cos(angle) * radius,
            center[1] + sin(angle) * radius
        ]
        positions.append(pos)

    return positions


def _calculate_character_zoom(ctx, max_size, tmpWidth, tmpHeight, uncropped_edge, rescaling_factor, customZoom):
    """Calculate zoom level for a single character image."""
    zoom_x = max_size[0] / tmpWidth
    zoom_y = max_size[1] / tmpHeight

    min_zoom = 1

    if not uncropped_edge:
        if zoom_x > zoom_y:
            min_zoom = zoom_x * ctx.proportional_zoom * rescaling_factor
        else:
            min_zoom = zoom_y * ctx.proportional_zoom * rescaling_factor
    else:
        has_all = 'u' in uncropped_edge and 'd' in uncropped_edge and 'l' in uncropped_edge and 'r' in uncropped_edge
        cropped_lr = 'l' not in uncropped_edge and 'r' not in uncropped_edge
        cropped_ud = 'u' not in uncropped_edge and 'd' not in uncropped_edge

        if has_all:
            min_zoom = customZoom * ctx.proportional_zoom * rescaling_factor
        elif cropped_lr:
            min_zoom = zoom_x * ctx.proportional_zoom * rescaling_factor
        elif cropped_ud:
            min_zoom = zoom_y * ctx.proportional_zoom * rescaling_factor
        else:
            min_zoom = customZoom * ctx.proportional_zoom * rescaling_factor

    if ctx.scale_fill_x and not ctx.scale_fill_y:
        min_zoom = zoom_x * ctx.proportional_zoom * rescaling_factor
    elif ctx.scale_fill_y and not ctx.scale_fill_x:
        min_zoom = zoom_y * ctx.proportional_zoom * rescaling_factor
    elif ctx.scale_fill_x and ctx.scale_fill_y:
        min_zoom = max(zoom_x, zoom_y) * rescaling_factor

    zoom = max(min_zoom, customZoom * min_zoom)

    cropped_lr = 'l' not in uncropped_edge and 'r' not in uncropped_edge
    cropped_ud = 'u' not in uncropped_edge and 'd' not in uncropped_edge

    if cropped_lr or cropped_ud:
        minZoomWidth = min_zoom * tmpWidth
        minZoomHeight = min_zoom * tmpHeight
        if cropped_lr and minZoomWidth != max_size[0]:
            correction_ratio = max_size[0] / minZoomWidth
            minZoomWidth = minZoomWidth * correction_ratio
            minZoomHeight = minZoomHeight * correction_ratio
            if (not cropped_ud) or (cropped_ud and minZoomHeight >= max_size[1]):
                zoom = zoom * correction_ratio
        if cropped_ud and minZoomHeight != max_size[1]:
            correction_ratio = max_size[1] / minZoomHeight
            minZoomWidth = minZoomWidth * correction_ratio
            minZoomHeight = minZoomHeight * correction_ratio
            if (not cropped_lr) or (cropped_lr and minZoomWidth >= max_size[0]):
                zoom = zoom * correction_ratio

    return zoom


def _draw_character_separators(ctx, separatorsPix, path_matrix, line_index, col_index, line,
                               curr_col_index, curr_line_index,
                               individual_paste_x, individual_paste_y, individual_max_size):
    """Draw separator lines between character images."""
    separator_right = False
    separator_down = False

    separatorHeight = round(ctx.separator_width * ctx.ratio[0])
    separatorWidth = round(ctx.separator_width * ctx.ratio[1])

    if ctx.flip_direction:
        separator_right = curr_col_index < len(line) - 1

        for l in range(line_index + 1, len(path_matrix)):
            if path_matrix[l][col_index] != None:
                separator_down = True
                break
    else:
        separator_down = curr_line_index < len(path_matrix) - 1

        for c in range(col_index + 1, len(line)):
            if line[c] != None:
                separator_right = True
                break

    if separator_right and ctx.separator_width > 0:
        painter = QPainter(separatorsPix)
        painter.setPen(
            QPen(QColor(ctx.separator_color_code), separatorWidth))
        painter.drawLine(
            individual_paste_x + individual_max_size[0],
            individual_paste_y,
            individual_paste_x + individual_max_size[0],
            individual_paste_y + individual_max_size[1],
        )
        painter.end()

    if separator_down and ctx.separator_width > 0:
        painter = QPainter(separatorsPix)
        painter.setPen(
            QPen(QColor(ctx.separator_color_code), separatorHeight))
        painter.drawLine(
            individual_paste_x,
            individual_paste_y + individual_max_size[1],
            individual_paste_x + individual_max_size[0],
            individual_paste_y + individual_max_size[1],
        )
        painter.end()


def paste_image_matrix(ctx, thumbnail, path_matrix, max_size, paste_coordinates, eyesight_matrix, rescaling_matrix, player_index=0, flip_p1=False, flip_p2=False, fill_x=True, fill_y=True, customZoom=1, horizontalAlign=50, verticalAlign=50, uncropped_edges=[]):
    num_line = len(path_matrix)

    image_ratio = (max(ctx.ratio[0], ctx.ratio[1]), max(ctx.ratio[0], ctx.ratio[1]))

    separatorsPix = QPixmap(thumbnail.width(), thumbnail.height())
    separatorsPix.fill(QColor(0, 0, 0, 0))

    debugPix = QPixmap(thumbnail.width(), thumbnail.height())
    debugPix.fill(QColor(0, 0, 0, 0))

    for line_index in range(0, len(path_matrix)):
        line = path_matrix[line_index]

        eyesight_line = []

        if eyesight_matrix and len(eyesight_matrix) >= line_index:
            eyesight_line = eyesight_matrix[line_index]

        num_col = len(line)

        for col_index in range(0, len(line))[::-1]:
            if path_matrix[line_index][col_index] == None:
                continue

            chars_in_col = 0
            chars_in_line = 0

            curr_col_index = 0
            curr_line_index = 0

            if ctx.flip_direction:
                for l in range(0, len(path_matrix)):
                    if l == line_index:
                        curr_line_index = chars_in_line
                    if path_matrix[l][col_index] != None:
                        chars_in_line += 1
                chars_in_col = len(line)
                curr_col_index = col_index
            else:
                for c in range(0, len(line)):
                    if c == col_index:
                        curr_col_index = chars_in_col
                    if line[c] != None:
                        chars_in_col += 1
                chars_in_line = len(path_matrix)
                curr_line_index = line_index

            individual_max_size = (
                round(max_size[0]/chars_in_col), round(max_size[1]/chars_in_line))
            image_path = line[col_index]

            individual_paste_x = round(
                paste_coordinates[0] + curr_col_index*individual_max_size[0])
            individual_paste_y = round(
                paste_coordinates[1] + curr_line_index*individual_max_size[1])

            if ctx.no_separator != 0:
                individual_max_size = (
                    max_size[0], round(max_size[1]/num_line))
                individual_paste_x = paste_coordinates[0]

            logger.info(f"Processing asset: {image_path}")

            pix = QPixmap(image_path, "RGBA")
            tmpWidth = int(pix.width() * image_ratio[0])
            tmpHeight = int(pix.height() * image_ratio[1])
            painter = QPainter(thumbnail)

            eyesight_coordinates = (tmpWidth/2, tmpHeight/2)

            if len(eyesight_line) >= col_index:
                if eyesight_line[col_index] != None:
                    eyesight_coordinates = (
                        eyesight_line[col_index][0] * image_ratio[0],
                        eyesight_line[col_index][1] * image_ratio[1]
                    )

            uncropped_edge = []

            if len(uncropped_edges) >= line_index:
                if len(uncropped_edges[line_index]) >= col_index:
                    uncropped_edge = uncropped_edges[line_index][col_index]

            if ctx.proportional_scaling:
                rescaling_factor = rescaling_matrix[line_index][col_index]
            else:
                rescaling_factor = 1

            zoom = _calculate_character_zoom(
                ctx, max_size, tmpWidth, tmpHeight, uncropped_edge, rescaling_factor, customZoom)

            customCenter = [horizontalAlign/100.0, verticalAlign/100.0]

            if ctx.no_separator != 0:
                customCenter = generate_multicharacter_positions(
                    num_col,
                    center=customCenter,
                    radius=ctx.no_separator_distance/100,
                    angle=ctx.no_separator_angle
                )[col_index]

            if player_index == 1:
                customCenter[0] = 1 - customCenter[0]

            if (player_index == 1 and flip_p2) or (player_index == 0 and flip_p1):
                customCenter[0] = 1 - customCenter[0]

            if not customCenter:
                customCenter = [0.5, 0.5]

            xx = -eyesight_coordinates[0] * zoom + \
                individual_max_size[0] * customCenter[0]
            yy = -eyesight_coordinates[1] * zoom + \
                individual_max_size[1] * customCenter[1]

            original_xx = xx
            original_yy = yy

            # Max move X
            maxMoveX = individual_max_size[0] - tmpWidth * zoom

            if 'l' not in uncropped_edge:
                if xx > 0:
                    xx = 0

            if 'r' not in uncropped_edge:
                if xx < maxMoveX:
                    xx = maxMoveX

            # Max move Y
            maxMoveY = individual_max_size[1] - tmpHeight * zoom

            if 'u' not in uncropped_edge:
                if yy > 0:
                    yy = 0

            if 'd' not in uncropped_edge:
                if yy < maxMoveY:
                    yy = maxMoveY

            flip = False

            if (player_index == 1 and flip_p2) or (player_index == 0 and flip_p1):
                flip = True

            area = QPixmap(int(individual_max_size[0]), int(
                individual_max_size[1]))
            area.fill(QColor(0, 0, 0, 0))

            areaPaint = QPainter(area)

            transformMode = Qt.TransformationMode.SmoothTransformation

            if not ctx.smooth_scale:
                transformMode = Qt.TransformationMode.FastTransformation

            areaPaint.drawPixmap(
                int(xx), int(yy),
                pix
                .scaled(
                    int(zoom*tmpWidth),
                    int(zoom*tmpHeight),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    transformMode
                )
            )

            areaPaint.end()

            if flip:
                area = area.transformed(QTransform().scale(-1, 1))

            painter.drawPixmap(
                int(individual_paste_x),
                int(individual_paste_y),
                area
            )
            painter.end()

            if ctx.is_preview:
                draw_text(
                    ctx,
                    debugPix,
                    QApplication.translate("Form", "Scale: {0}").format(
                        "{0:.2f}".format(zoom)) + '%'+"\n",
                    ctx.font_1, 24, (0, 0, 0),
                    (round(paste_coordinates[0] + col_index *
                     round(max_size[0]/num_col)), individual_paste_y+2),
                    (round(max_size[0]/num_col), 24),
                    True,
                    (200, 200, 200),
                    (4, 2)
                )

                draw_text(
                    ctx,
                    debugPix,
                    QApplication.translate("Form", "Eyesight offset: ({0}, {1})").format(
                        int(original_xx - xx), int(original_yy - yy)),
                    ctx.font_1, 24, (0, 0, 0),
                    (round(paste_coordinates[0] + col_index *
                     round(max_size[0]/num_col)), individual_paste_y+22),
                    (round(max_size[0]/num_col), 24),
                    True,
                    (200, 200, 200),
                    (4, 2)
                )

            if ctx.no_separator == 0:
                _draw_character_separators(
                    ctx, separatorsPix, path_matrix, line_index, col_index, line,
                    curr_col_index, curr_line_index,
                    individual_paste_x, individual_paste_y, individual_max_size)

        painter = QPainter(thumbnail)
        painter.drawPixmap(0, 0, separatorsPix)
        if ctx.is_preview:
            painter.drawPixmap(0, 0, debugPix)
        painter.end()

    return (thumbnail)


def paste_characters(ctx, thumbnail, data, all_eyesight, used_assets, flip_p1=False, flip_p2=False, fill_x=True, fill_y=True, zoom=1, horizontalAlign=50, verticalAlign=50, scoreboardNumber=1):
    max_x_size = round(
        ctx.template_data["character_images"]["dimensions"]["x"]*ctx.ratio[0]/2)
    max_y_size = round(
        ctx.template_data["character_images"]["dimensions"]["y"]*ctx.ratio[1])
    max_size = (max_x_size, max_y_size)
    origin_x_coordinates = [round(ctx.template_data["character_images"]["position"]["x"]*ctx.ratio[0]), round(
        ctx.template_data["character_images"]["position"]["x"]*ctx.ratio[0])+max_x_size]
    origin_y_coordinates = [
        round(ctx.template_data["character_images"]["position"]["y"]*ctx.ratio[1]),
        round(ctx.template_data["character_images"]["position"]["y"]*ctx.ratio[1])
    ]

    path_matrices = []
    eyesight_matrices = []
    uncropped_edge_matrices = []
    average_size = None
    rescaling_matrices = []

    for i in [0, 1]:
        team_index = i+1

        path_matrix = []
        eyesight_matrix = []
        uncropped_edge_matrix = []
        rescaling_matrix = []

        try:
            current_team = find(
                f"score.{scoreboardNumber}.team.{team_index}.player", data)
        except Exception as e:
            logger.error(traceback.format_exc())
            return

        for player_key in current_team.keys():
            character_list = []
            eyesight_list = []
            uncropped_edge_list = []
            rescaling_list = []
            characters = find(f"{player_key}.character", current_team)
            for character_key in characters.keys():
                try:
                    image_path = find(
                        f"{character_key}.assets.{used_assets}.asset", characters)
                    eyesight_coordinates = None

                    character_path = find(
                        f"{character_key}.assets.{used_assets}", characters)

                    # Eyesight
                    if character_path.get("eyesight"):
                        eyesight_coordinates = (
                            character_path.get("eyesight")["x"], character_path.get("eyesight")["y"])

                    # Uncropped edges
                    uncropped_edges = None

                    if character_path.get("uncropped_edge") is not None:
                        uncropped_edges = character_path.get("uncropped_edge")
                    else:
                        uncropped_edges = []

                    # Average size
                    if character_path.get("average_size") is not None:
                        average_size = character_path.get("average_size")

                    # Rescale
                    rescale_factor = 1

                    if character_path.get("rescaling_factor") is not None:
                        rescale_factor = character_path.get("rescaling_factor")

                    if image_path:
                        character_list.append(image_path)
                        eyesight_list.append(eyesight_coordinates)
                        uncropped_edge_list.append(uncropped_edges)
                        rescaling_list.append(rescale_factor)
                except KeyError:
                    logger.warning(f"Missing asset data for character key in player {player_key}")
            if character_list:
                # For team 1, characters must come from the center
                # so we have to reverse the array
                # but only when drawing separators
                if i == 0 and not ctx.no_separator and not ctx.flip_direction:
                    character_list.reverse()
                    eyesight_list.reverse()
                    uncropped_edge_list.reverse()
                    rescaling_list.reverse()

                if ctx.no_separator:
                    path_matrix.extend(character_list)
                    eyesight_matrix.extend(eyesight_list)
                    uncropped_edge_matrix.extend(uncropped_edge_list)
                    rescaling_matrix.extend(rescaling_list)
                else:
                    path_matrix.append(character_list)
                    eyesight_matrix.append(eyesight_list)
                    uncropped_edge_matrix.append(uncropped_edge_list)
                    rescaling_matrix.append(rescaling_list)

        if ctx.no_separator:
            path_matrix = [path_matrix]
            eyesight_matrix = [eyesight_matrix]
            uncropped_edge_matrix = [uncropped_edge_matrix]
            rescaling_matrix = [rescaling_matrix]

        # Transpose character lists
        if not ctx.no_separator and ctx.flip_direction:
            path_matrix = list(
                map(list, itertools.zip_longest(*path_matrix, fillvalue=None)))
            eyesight_matrix = list(
                map(list, itertools.zip_longest(*eyesight_matrix, fillvalue=None)))
            uncropped_edge_matrix = list(
                map(list, itertools.zip_longest(*uncropped_edge_matrix, fillvalue=None)))
            rescaling_matrix = list(
                map(list, itertools.zip_longest(*rescaling_matrix, fillvalue=None)))

        path_matrices.append(path_matrix)
        eyesight_matrices.append(eyesight_matrix)
        uncropped_edge_matrices.append(uncropped_edge_matrix)
        rescaling_matrices.append(rescaling_matrix)

    # Calculate proportional scaling
    ctx.proportional_zoom = 1

    if average_size is not None:
        ctx.proportional_zoom = 0
        ctx.proportional_zoom = max(
            ctx.proportional_zoom, max_size[0] / ctx.ratio[0] / average_size.get("x") * 1.2)
        ctx.proportional_zoom = max(
            ctx.proportional_zoom, max_size[1] / ctx.ratio[1] / average_size.get("y") * 1.2)
    else:
        max_width = 0
        max_height = 0

        for path_matrix in path_matrices:
            for player_pathes in path_matrix:
                for path in player_pathes:
                    pix = QPixmap(path)
                    pix = pix.scaled(
                        int(pix.width() * ctx.ratio[0]),
                        int(pix.height() * ctx.ratio[1]),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    max_width = max(max_width, pix.width())
                    max_height = max(max_height, pix.height())

        if max_width != 0 and max_height != 0:
            ctx.proportional_zoom = max(
                ctx.proportional_zoom, max_size[0] / max_width * 1.2)
            ctx.proportional_zoom = max(
                ctx.proportional_zoom, max_size[1] / max_height * 1.2)

    for i in [0, 1]:
        team_index = i+1

        path_matrix = path_matrices[i]
        eyesight_matrix = eyesight_matrices[i]
        uncropped_edge_matrix = uncropped_edge_matrices[i]
        rescale_matrix = rescaling_matrices[i]

        paste_x = origin_x_coordinates[i]
        paste_y = origin_y_coordinates[i]
        paste_coordinates = (paste_x, paste_y)

        thumbnail = paste_image_matrix(
            ctx, thumbnail, path_matrix, max_size, paste_coordinates, eyesight_matrix, rescale_matrix, i, flip_p1, flip_p2,
            fill_x, fill_y, zoom, horizontalAlign=horizontalAlign, verticalAlign=verticalAlign, uncropped_edges=uncropped_edge_matrix)

    return (thumbnail)


def _find_max_font_size(font, container_height, padding_y):
    """Binary search for the largest font size that fits the container height."""
    lo, hi = 1, font.pixelSize()
    while lo < hi:
        mid = (lo + hi + 1) // 2
        font.setPixelSize(mid)
        fm = QFontMetricsF(font)
        if fm.height() + 2 * padding_y <= container_height:
            lo = mid
        else:
            hi = mid - 1
    font.setPixelSize(lo)
    return lo


def _find_text_fit(font, text, container_width, container_height, padding, stretch_limit):
    """Binary search for font size and stretch that fits text in the container."""
    max_font_size = font.pixelSize()
    stretch = 100

    fm = QFontMetricsF(font)
    while fm.width(text) + 2 * padding[0] > int(container_width):
        if stretch <= stretch_limit:
            # Reduce font size via binary search over remaining range
            lo, hi = 1, max_font_size - 1
            if lo > hi:
                raise ValueError("Text too small, size cannot be negative")
            while lo < hi:
                mid = (lo + hi + 1) // 2
                font.setPixelSize(mid)
                font.setStretch(stretch)
                test_fm = QFontMetricsF(font)
                if test_fm.width(text) + 2 * padding[0] <= int(container_width):
                    lo = mid
                else:
                    hi = mid - 1
            max_font_size = lo
            font.setPixelSize(max_font_size)
            font.setStretch(stretch)
            break
        else:
            stretch -= 1
            font.setStretch(stretch)
        fm = QFontMetricsF(font)

    return max_font_size, stretch


def draw_text(ctx, thumbnail, text, font_data, max_font_size, color, pos, container_size, outline, outline_color, padding=(32, 16), skew=(0, 0)):
    pix = QPixmap(int(container_size[0]), int(container_size[1]))
    pix.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    font = QFont()
    family = font_data["name"]
    font.setFamily(family)
    font.setPixelSize(int(max_font_size))
    type = font_data["fontPath"]
    if "bold" in type.lower():
        font.setBold(True)
    if "italic" in type.lower():
        font.setItalic(True)

    # Binary search for max font size that fits height
    max_font_size = _find_max_font_size(font, int(container_size[1]), padding[1])

    # Binary search for stretch/size that fits width
    max_font_size, stretch = _find_text_fit(
        font, text, container_size[0], container_size[1], padding,
        ctx.template_data["lower_text_stretch_limit"])

    fontMetrics = QFontMetricsF(font)

    text_x = round((padding[0]))
    text_y = round((padding[1]))
    text_coordinates = (text_x, text_y)

    if outline:
        stroke_width = round(8*ctx.ratio[1])
    else:
        stroke_width = 0

    path = QPainterPath()

    painter.setFont(font)

    pen = QPen()

    if isinstance(color, tuple):
        color = [color] * len(text)

    pos_x = int(text_coordinates[0]) + (container_size[0] -
                                        padding[0]*2) / 2 - fontMetrics.width(text)/2

    for i, char in enumerate(text):
        path.clear()

        pen.setWidth(stroke_width)
        pen.setColor(QColor(
            outline_color[0],
            outline_color[1],
            outline_color[2]
        ))
        painter.setPen(pen)

        painter.setBrush(QColor(
            color[i][0],
            color[i][1],
            color[i][2]
        ))

        path.addText(
            pos_x,
            int(text_coordinates[1]) + fontMetrics.height()/4 +
            (container_size[1]-padding[1]*2)/2,
            font,
            char
        )

        painter.drawPath(path)

        pen.setWidth(0)
        pen.setColor(QColor(0, 0, 0, 0))
        painter.setPen(pen)
        painter.drawPath(path)

        pos_x += fontMetrics.width(char)

    painter.end()

    pix = pix.transformed(QTransform().shear(radians(skew[0]), radians(
        skew[1])), Qt.TransformationMode.SmoothTransformation)

    # Paste pixmap in the thumbnail
    painter = QPainter(thumbnail)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.drawPixmap(pos[0], pos[1], pix)
    painter.end()


def paste_player_text(ctx, thumbnail, data, use_team_names=False, use_sponsors=True, scoreboardNumber=1):
    text_player_max_dimensions = (round(ctx.template_data["character_images"]["dimensions"]["x"]*ctx.ratio[0]/2.0), round(
        ctx.template_data["player_text"]["dimensions"]["y"]*ctx.ratio[1]))
    text_player_coordinates = [
        (round((ctx.template_data["character_images"]["position"]["x"])*ctx.ratio[0]), round(
            (ctx.template_data["player_text"]["height_center"]-(ctx.template_data["player_text"]["dimensions"]["y"]/2.0))*ctx.ratio[1])),
        (round((ctx.template_data["character_images"]["position"]["x"]+(2*ctx.template_data["character_images"]["dimensions"]["x"]/4.0))*ctx.ratio[0]),
         round((ctx.template_data["player_text"]["height_center"]-(ctx.template_data["player_text"]["dimensions"]["y"]/2.0))*ctx.ratio[1]))
    ]

    font_path = ctx.font_1
    text_size = ctx.template_data["initial_font_size"]*ctx.ratio[1]
    player_text_color = ctx.text_color[0]

    for i in [0, 1]:
        team_index = i+1
        player_list = []
        color_mask = []
        final_color_mask = []

        if use_team_names and find(f"score.{scoreboardNumber}.team.{team_index}.teamName", data):
            player_name = find(
                f"score.{scoreboardNumber}.team.{team_index}.teamName", data)
            final_color_mask = player_text_color["font_color"]
        else:
            current_team = find(
                f"score.{scoreboardNumber}.team.{team_index}.player", data)
            for key in current_team.keys():
                current_data = ""
                individual_color_mask = []

                team = current_team[key].get("team", "")
                if team and use_sponsors:
                    current_data += team+" "
                    individual_color_mask += [ctx.sponsor_color[i]] * len(team+" ")

                current_data += current_team[key].get("name", "")
                individual_color_mask += [player_text_color["font_color"]
                                          ] * len(current_team[key].get("name", ""))

                if current_data:
                    current_data = current_data.strip()

                if (not use_sponsors) or (not current_data):
                    current_data = current_team[key].get("name", "")
                    individual_color_mask += [
                        player_text_color["font_color"]] * len(current_data)
                    if current_data:
                        current_data = current_data.strip()
                if current_data:
                    player_list.append(current_data)
                    color_mask.append(individual_color_mask)

            player_name = " / ".join(player_list)

            if not color_mask:
                final_color_mask = [player_text_color["font_color"]] * len(player_name)
            else:
                final_color_mask = color_mask[0]

            if len(color_mask) > 1:
                for mask in color_mask[1:]:
                    final_color_mask += [player_text_color["font_color"]] * 3
                    final_color_mask += mask

        if use_team_names or len(player_list) > 1:
            player_type = "team"
        else:
            player_type = "player"

        draw_text(
            ctx,
            thumbnail,
            player_name,
            font_path,
            text_size,
            final_color_mask,
            text_player_coordinates[i],
            text_player_max_dimensions,
            player_text_color["has_outline"],
            player_text_color["outline_color"],
            (
                round(ctx.template_data["player_text"]["x_offset"]*ctx.ratio[0]),
                round(ctx.template_data["player_text"]["y_padding"]*ctx.ratio[1])
            ),
            (
                ctx.template_data["player_text"].get("skew", {}).get("x", 0),
                ctx.template_data["player_text"].get("skew", {}).get("y", 0)
            )
        )


def paste_round_text(ctx, thumbnail, data, display_phase=True, scoreboardNumber=1):
    if display_phase:
        if ctx.template_data["info_text"]["horizontal"]:
            round_text_pos = (round(ctx.template_data["info_text"]["x_position"]*ctx.ratio[0]), round((ctx.template_data["info_text"]["height_center"]-(
                ctx.template_data["info_text"]["dimensions"]["y"]/2.0))*ctx.ratio[1]))
            phase_text_pos = (round(ctx.template_data["info_text"]["x_position"]*ctx.ratio[0])+round((ctx.template_data["info_text"]["dimensions"]["x"]/2.0)*ctx.ratio[0]), round(
                (ctx.template_data["info_text"]["height_center"]-(ctx.template_data["info_text"]["dimensions"]["y"]/2.0))*ctx.ratio[1]))

            text_max_dimensions = (round((ctx.template_data["info_text"]["dimensions"]["x"]/2.0)*ctx.ratio[0]), round(
                ctx.template_data["info_text"]["dimensions"]["y"]*ctx.ratio[1]))
        else:
            y_0 = ctx.template_data["info_text"]["height_center"] - \
                (ctx.template_data["info_text"]["dimensions"]["y"]/2.0)
            y_1 = ctx.template_data["info_text"]["height_center"]
            phase_text_pos = (
                round(ctx.template_data["info_text"]["x_position"]*ctx.ratio[0]), round(y_0*ctx.ratio[1]))
            round_text_pos = (
                round(ctx.template_data["info_text"]["x_position"]*ctx.ratio[0]), round(y_1*ctx.ratio[1]))

            text_max_dimensions = (round((ctx.template_data["info_text"]["dimensions"]["x"])*ctx.ratio[0]), round(
                (ctx.template_data["info_text"]["dimensions"]["y"]/2.0)*ctx.ratio[1]))

        text_size = ctx.template_data["initial_font_size"]*ctx.ratio[1]
        y_padding = round(ctx.template_data["info_text"]["y_padding"]*ctx.ratio[1])
        if not ctx.template_data["info_text"]["horizontal"]:
            y_padding = round(y_padding/2.0)

        draw_text(
            ctx,
            thumbnail,
            find(f"score.{scoreboardNumber}.phase", data),
            ctx.font_2,
            text_size,
            ctx.text_color[1]["font_color"],
            phase_text_pos,
            text_max_dimensions,
            ctx.text_color[1]["has_outline"],
            ctx.text_color[1]["outline_color"],
            (
                round(ctx.template_data["info_text"]["x_offset"]*ctx.ratio[0]/2.0),
                y_padding
            ),
            (
                ctx.template_data["info_text"].get("skew", {}).get("x", 0),
                ctx.template_data["info_text"].get("skew", {}).get("y", 0)
            )
        )

        draw_text(
            ctx,
            thumbnail,
            find(f"score.{scoreboardNumber}.match", data),
            ctx.font_2,
            text_size,
            ctx.text_color[1]["font_color"],
            round_text_pos,
            text_max_dimensions,
            ctx.text_color[1]["has_outline"],
            ctx.text_color[1]["outline_color"],
            (
                round(ctx.template_data["info_text"]["x_offset"]*ctx.ratio[0]/2.0),
                y_padding
            ),
            (
                ctx.template_data["info_text"].get("skew", {}).get("x", 0),
                ctx.template_data["info_text"].get("skew", {}).get("y", 0)
            )
        )
    else:
        round_text_pos = (round(ctx.template_data["info_text"]["x_position"]*ctx.ratio[0]), round((ctx.template_data["info_text"]["height_center"]-(
            ctx.template_data["info_text"]["dimensions"]["y"]/2.0))*ctx.ratio[1]))
        text_max_dimensions = (round((ctx.template_data["info_text"]["dimensions"]["x"])*ctx.ratio[0]), round(
            ctx.template_data["info_text"]["dimensions"]["y"]*ctx.ratio[1]))

        text_size = ctx.template_data["initial_font_size"]*ctx.ratio[1]

        draw_text(
            ctx,
            thumbnail,
            find(f"score.{scoreboardNumber}.match", data),
            ctx.font_2,
            text_size,
            ctx.text_color[1]["font_color"],
            round_text_pos,
            text_max_dimensions,
            ctx.text_color[1]["has_outline"],
            ctx.text_color[1]["outline_color"],
            (round(ctx.template_data["info_text"]["x_offset"]*ctx.ratio[0]),
             round(ctx.template_data["info_text"]["y_padding"]*ctx.ratio[1]))
        )


def paste_main_icon(ctx, thumbnail, icon_path):
    max_x_size = round(
        ctx.template_data["icons_position"]["main"]["dimensions"]["x"]*ctx.ratio[0])
    max_y_size = round(
        ctx.template_data["icons_position"]["main"]["dimensions"]["y"]*ctx.ratio[1])
    max_size = (max_x_size, max_y_size)

    icon_image = QPixmap(icon_path, 'RGBA')
    if icon_image.isNull():
        return thumbnail
    icon_size = calculate_new_dimensions(icon_image.size(), max_size)
    icon_image = icon_image.transformed(
        QTransform().scale(
            icon_size[0]/icon_image.width(), icon_size[1]/icon_image.height()),
        Qt.TransformationMode.SmoothTransformation)

    x_offset = ctx.template_data["base_ratio"]["x"] / 2.0
    if ctx.template_data["icons_position"]["bind_to_character_images"]:
        x_offset = ctx.template_data["character_images"]["dimensions"]["x"] / \
            2.0 + ctx.template_data["character_images"]["position"]["x"]

    y_offset = ctx.template_data["icons_position"]["y_offset"]
    if ctx.template_data["icons_position"]["bind_to_character_images"]:
        y_offset = y_offset + \
            ctx.template_data["character_images"]["position"]["y"]
    if ctx.template_data["icons_position"]["align"].lower() == "bottom":
        y_offset = ctx.template_data["base_ratio"]["y"] - \
            ctx.template_data["icons_position"]["y_offset"] - \
            icon_image.height()/ctx.ratio[1]
        if ctx.template_data["icons_position"]["bind_to_character_images"]:
            y_offset = ctx.template_data["character_images"]["position"]["y"] + ctx.template_data["character_images"]["dimensions"]["y"] - \
                ctx.template_data["icons_position"]["y_offset"] - \
                icon_image.height()/ctx.ratio[1]

    icon_x = round(x_offset*ctx.ratio[0] - icon_size[0]/2)
    icon_y = y_offset*ctx.ratio[1]
    icon_coordinates = (icon_x, icon_y)
    composite_image = create_composite_image(
        icon_image, thumbnail.size(), icon_coordinates)

    painter = QPainter(thumbnail)
    painter.drawPixmap(0, 0, composite_image)
    painter.end()
    return (thumbnail)


def paste_side_icon(ctx, thumbnail, icon_path_list):
    if len(icon_path_list) > 2:
        raise (ValueError(msg="Error: icon_path_list has 3 or more elements"))

    max_x_size = round(
        ctx.template_data["icons_position"]["side"]["dimensions"]["x"]*ctx.ratio[0])
    max_y_size = round(
        ctx.template_data["icons_position"]["side"]["dimensions"]["y"]*ctx.ratio[0])
    max_size = (max_x_size, max_y_size)

    for index in range(0, len(icon_path_list)):
        icon_path = icon_path_list[index]
        if icon_path:
            icon_image = QPixmap(icon_path, 'RGBA')
            icon_size = calculate_new_dimensions(icon_image.size(), max_size)
            icon_image = icon_image.transformed(
                QTransform().scale(
                    icon_size[0]/icon_image.width(), icon_size[1]/icon_image.height()),
                Qt.TransformationMode.SmoothTransformation)

            y_offset = ctx.template_data["icons_position"]["y_offset"]
            if ctx.template_data["icons_position"]["bind_to_character_images"]:
                y_offset = y_offset + \
                    ctx.template_data["character_images"]["position"]["y"]
            if ctx.template_data["icons_position"]["align"].lower() == "bottom":
                y_offset = ctx.template_data["base_ratio"]["y"] - \
                    ctx.template_data["icons_position"]["y_offset"] - \
                    icon_image.height()/ctx.ratio[1]
                if ctx.template_data["icons_position"]["bind_to_character_images"]:
                    y_offset = ctx.template_data["character_images"]["position"]["y"] + ctx.template_data["character_images"]["dimensions"]["y"] - \
                        ctx.template_data["icons_position"]["y_offset"] - \
                        icon_image.height()/ctx.ratio[1]

            x_offset = index*ctx.template_data["base_ratio"]["x"]
            if ctx.template_data["icons_position"]["bind_to_character_images"]:
                x_offset = ctx.template_data["character_images"]["position"]["x"] + \
                    index*ctx.template_data["character_images"]["dimensions"]["x"]
            x_offset = x_offset - \
                round(ctx.template_data["icons_position"]
                      ["side"]["x_offset"]) * ((index*2)-1)
            icon_x = x_offset*ctx.ratio[0] - index*icon_size[0]
            icon_y = y_offset*ctx.ratio[1]

            icon_coordinates = (round(icon_x), round(icon_y))
            composite_image = create_composite_image(
                icon_image, thumbnail.size(), icon_coordinates)

            painter = QPainter(thumbnail)
            painter.drawPixmap(0, 0, composite_image)
            painter.end()
    return (thumbnail)


def createFalseData(gameAssetManager: TSHGameAssetManager = None, used_assets: str = None):
    # Array [{"name", "asset"}]
    chars = []

    if gameAssetManager and len(gameAssetManager.instance.characters.keys()) > 0:

        for i in range(4):
            asset = None
            if not asset:
                key = list(gameAssetManager.instance.characters.keys())[
                    random.randint(0, len(gameAssetManager.instance.characters)-1)]

                character = gameAssetManager.instance.characters[key]
                skin = random.randint(
                    0, len(gameAssetManager.instance.characters[key]))

                data = gameAssetManager.instance.GetCharacterAssets(
                    character.get("codename"), skin)

                asset = data.get(used_assets)

                if not asset:
                    asset = data.get("full")
                if not asset:
                    asset = data.get("portrait")
                if not asset:
                    asset = data.get("base_files/icon")

            name = key
            if character.get("locale"):
                locale = TSHLocaleHelper.programLocale
                if locale.replace("-", "_") in character["locale"]:
                    name = character["locale"][locale.replace("-", "_")]
                elif re.split("-|_", locale)[0] in character["locale"]:
                    name = character["locale"][re.split("-|_", locale)[0]]

            chars.append({
                "name": name,
                "team": gameAssetManager.instance.selectedGame.get("codename").upper(),
                "asset": {
                    "assets": {
                        "full": asset
                    },
                    "codename": character["codename"],
                    "name": key,
                    "skin": "0"
                }
            })
    else:
        for i in range(4):
            chars.append({
                "name": QApplication.translate("app", "Player {0}").format(i+1),
                "team": QApplication.translate("app", "Sponsor {0}").format(i+1),
                "asset": {
                    "assets": {
                        "full": {
                            "asset": f"./assets/mock_data/mock_asset/full_character_{i}.png",
                            "eyesight": {
                                "x": 540,
                                "y": 126
                            }
                        }
                    },
                    "codename": "character",
                    "name": "Character",
                    "skin": "0"
                },
            })

    data = {
        "game": {
            "codename": "test",
            "name": "Test",
            "smashgg_id": 0
        },
        "score": {
            "1": {
                "best_of": 0,
                "match": TSHLocaleHelper.matchNames.get("winners_final"),
                "phase": TSHLocaleHelper.phaseNames.get("group").format("A"),
                "team": {
                    "1": {
                        "losers": False,
                        "player": {
                            "1": {
                                "character": {
                                    "1": chars[0]["asset"],
                                    "2": chars[1]["asset"]
                                },
                                "country": {},
                                "mergedName": f"{chars[0]['team']} | {chars[0]['name']}",
                                "name": chars[0]["name"],
                                "state": {},
                                "team": chars[0]["team"]
                            }
                        },
                        "score": 0,
                        "teamName": QApplication.translate("app", "Team {0}").format("A")
                    },
                    "2": {
                        "losers": False,
                        "player": {
                            "1": {
                                "character": {
                                    "1": chars[2]["asset"]
                                },
                                "country": {},
                                "mergedName": f"{chars[2]['team']} | {chars[2]['name']}",
                                "name": chars[2]["name"],
                                "state": {},
                                "team": chars[2]["team"]
                            },
                            "2": {
                                "character": {
                                    "1": chars[3]["asset"]
                                },
                                "country": {},
                                "mergedName": f"{chars[3]['team']} | {chars[3]['name']}",
                                "name": chars[3]["name"],
                                "state": {},
                                "team": chars[3]["team"]
                            }
                        },
                        "score": 0,
                        "teamName": QApplication.translate("app", "Team {0}").format("B")
                    }
                }
            }
        }
    }
    return data


def remove_special_chars(input_str: str):
    invalid = '<>:"/\\|?* '
    for char in invalid:
        input_str = input_str.replace(char, "")
    return input_str


def generate(settingsManager, isPreview=False, gameAssetManager=None, scoreboardNumber=1):
    # can't import SettingsManager (ImportError: attempted relative import beyond top-level package) so.. parameter ?
    settings = settingsManager.Get("thumbnail_config")

    ctx = ThumbnailContext()

    try:
        with open(settings.get("thumbnail_type"), 'rt') as template_data_file:
            ctx.template_data = json.loads(template_data_file.read())
    except Exception as e:
        logger.error(f"Failed to load thumbnail type: {e}")
        if isPreview:
            return
        raise Exception(f"Thumbnail type could not be loaded")

    ctx.is_preview = isPreview

    data_path = "./out/program_state.json"
    out_path = "./out/thumbnails" if not isPreview else TSHResolve("tmp/thumbnail")
    tmp_path = TSHResolve("tmp")

    # IMG PATH
    foreground_path = settings.get("foreground_path")
    if not foreground_path:
        foreground_path = ctx.template_data["default_foreground"]
    background_path = settings.get("background_path")
    if not background_path:
        background_path = ctx.template_data["default_background"]
    if not os.path.isfile(background_path):
        raise Exception(f"Background {background_path} doesn't exist !")
    main_icon_path = settings.get("main_icon_path", "")
    side_icon_list = [
        deep_get(settings, f"side_icon_list.L", ""),
        deep_get(settings, f"side_icon_list.R", "")
    ]
    # not blocking so empty
    if side_icon_list[0] and not os.path.isfile(side_icon_list[0]):
        logger.info(f"Top Left Icon {side_icon_list[0]} doesn't exist !")
        side_icon_list[0] = ''
    if side_icon_list[1] and not os.path.isfile(side_icon_list[1]):
        logger.info(f"Top Right Icon {side_icon_list[1]} doesn't exist !")
        side_icon_list[1] = ''
    # BOOLEAN
    display_phase = deep_get(settings, f"display_phase")
    use_team_names = deep_get(settings, "use_team_names")
    use_sponsors = deep_get(settings, "use_sponsors")

    font_list = [
        {
            "name": "Roboto Condensed",
            "type": "Bold",
            "fontPath": "./assets/font/RobotoCondensed.ttf"
        },
        {
            "name": "Roboto Condensed",
            "type": "Bold",
            "fontPath": "./assets/font/RobotoCondensed.ttf"
        }
    ]
    if deep_get(settings, f"player_font"):
        font_list[0] = deep_get(settings, f"player_font")
    if deep_get(settings, f"phase_font"):
        font_list[1] = deep_get(settings, f"phase_font")

    ctx.text_color = [
        {
            "font_color": color_code_to_tuple(settings.get("player_font_color", "#FFFFFF")),
            "has_outline": deep_get(settings, f"player_outline", True),
            "outline_color": color_code_to_tuple(deep_get(settings, f"player_outline_color", "#FFFFFF"))
        },
        {
            "font_color": color_code_to_tuple(settings.get("phase_font_color", "#FFFFFF")),
            "has_outline": deep_get(settings, f"phase_outline", True),
            "outline_color": color_code_to_tuple(deep_get(settings, f"phase_outline_color", "#FFFFFF"))
        }
    ]

    ctx.sponsor_color = [
        color_code_to_tuple(settings.get("sponsor_font_color_1", "#FFFFFF")),
        color_code_to_tuple(settings.get("sponsor_font_color_2", "#FFFFFF"))
    ]

    zoom = 1

    try:
        with open(data_path, 'rt', encoding='utf-8') as f:
            data = json.loads(f.read())
        # if data missing
        if not data.get("game").get("codename"):
            raise Exception(QApplication.translate(
                "thumb_app", "Please select a game first"))
        # - if more than one player (team of 2,3 etc), not necessary because test is made on paste_player_text
        for i in [1, 2]:
            if 'name' not in data.get("score").get(str(scoreboardNumber)).get("team").get(str(i)).get("player").get("1"):
                raise Exception(QApplication.translate(
                    "thumb_app", "Player {0} tag missing").format(i))
        game_codename = data.get("game").get("codename")
        used_assets = deep_get(settings, f"game.{game_codename}.asset_pack")
        asset_data_path = f"./user_data/games/{game_codename}/{used_assets}/config.json"
        zoom = deep_get(settings, f"game.{game_codename}.zoom", 100)/100
        flip_p1 = deep_get(settings, f"game.{game_codename}.flip_p1")
        flip_p2 = deep_get(settings, f"game.{game_codename}.flip_p2")
        ctx.smooth_scale = deep_get(settings, f"game.{game_codename}.smooth_scale")
    except Exception as e:
        logger.warning(f"Failed to load program state, falling back: {e}")
        if isPreview:
            game_codename = data.get("game", {}).get("codename", "test")
            data = createFalseData(gameAssetManager, deep_get(
                settings, f"game.{game_codename}.asset_pack"))
            used_assets = "full"
            asset_data_path = f"./assets/mock_data/mock_asset/config.json"
            zoom = deep_get(settings, f"game.{game_codename}.zoom", 100)/100
            flip_p1 = deep_get(settings, f"game.{game_codename}.flip_p1")
            flip_p2 = deep_get(settings, f"game.{game_codename}.flip_p2")
            ctx.smooth_scale = deep_get(
                settings, f"game.{game_codename}.smooth_scale")
        else:
            raise e

    try:
        with open(asset_data_path, 'rt', encoding='utf-8') as f:
            all_eyesight = json.loads(f.read()).get("eyesights", {})
    except Exception as e:
        logger.warning(f"Failed to load eyesight data from {asset_data_path}: {e}")
        all_eyesight = {}

    Path(tmp_path).mkdir(parents=True, exist_ok=True)

    ctx.font_1 = font_list[0]
    ctx.font_2 = font_list[1]

    ctx.separator_color_code = deep_get(settings, f"separator.color", "#FFFFFF")
    ctx.separator_width = deep_get(settings, f"separator.width", 5)

    Path(out_path).mkdir(parents=True, exist_ok=True)

    foreground = QPixmap(foreground_path, "RGBA")
    background = QPixmap(background_path, "RGBA")

    if isPreview:
        background = background.scaled(
            int(background.width()/2),
            int(background.height()/2),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

    ctx.ratio = (background.width()/ctx.template_data["base_ratio"]["x"],
                 background.height()/ctx.template_data["base_ratio"]["y"])

    ctx.scale_fill_x = deep_get(settings, f"game.{game_codename}.scaleFillX", 0)
    ctx.scale_fill_y = deep_get(settings, f"game.{game_codename}.scaleFillY", 0)

    ctx.no_separator = deep_get(
        settings, f"game.{game_codename}.hideSeparators", 0)

    ctx.no_separator_angle = deep_get(
        settings, f"game.{game_codename}.noSeparatorAngle", 45)

    ctx.no_separator_distance = deep_get(
        settings, f"game.{game_codename}.noSeparatorDistance", 30)

    ctx.proportional_scaling = deep_get(
        settings, f"game.{game_codename}.proportionalScaling", True)

    ctx.flip_direction = deep_get(
        settings, f"game.{game_codename}.flipSeparators", 0)

    foreground = foreground.scaled(
        background.width(),
        background.height(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )

    thumbnail = QPixmap(background.width(), background.height())
    thumbnail.fill(QColor(0, 0, 0, 0))

    composite_image = create_composite_image(
        background, thumbnail.size(), (0, 0))

    painter = QPainter(thumbnail)
    painter.drawPixmap(0, 0, composite_image)
    painter.drawPixmap(0, 0, background)
    painter.end()

    horizontalAlign = deep_get(
        settings, f"game.{game_codename}.align.horizontal", 50)
    verticalAlign = deep_get(
        settings, f"game.{game_codename}.align.vertical", 40)

    thumbnail = paste_characters(
        ctx, thumbnail, data, all_eyesight, used_assets, flip_p1, flip_p2, fill_x=True, fill_y=True, zoom=zoom, horizontalAlign=horizontalAlign, verticalAlign=verticalAlign, scoreboardNumber=scoreboardNumber)
    if thumbnail is None:
        return
    composite_image = create_composite_image(
        foreground, thumbnail.size(), (0, 0))

    painter = QPainter(thumbnail)
    painter.drawPixmap(0, 0, composite_image)
    painter.end()

    paste_player_text(ctx, thumbnail, data, use_team_names,
                      use_sponsors, scoreboardNumber=scoreboardNumber)
    paste_round_text(ctx, thumbnail, data, display_phase,
                     scoreboardNumber=scoreboardNumber)
    thumbnail = paste_main_icon(ctx, thumbnail, main_icon_path)
    thumbnail = paste_side_icon(ctx, thumbnail, side_icon_list)

    # TODO get char name
    if not isPreview:
        tag_player1 = find(
            f"score.{scoreboardNumber}.team.1.player.1.name", data)
        tag_player2 = find(
            f"score.{scoreboardNumber}.team.2.player.1.name", data)
        thumbnail_filename = f"{remove_special_chars(tag_player1)}-vs-{remove_special_chars(tag_player2)}-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d-%H-%M-%S')}"
        thumbnail.save(f"{out_path}/{thumbnail_filename}.png")
        thumbnail.save(f"{out_path}/{thumbnail_filename}.jpg")
        if os.path.isdir(tmp_path):
            shutil.rmtree(tmp_path)
        logger.info(
            f"Thumbnail successfully saved as {out_path}/{thumbnail_filename}.png and {out_path}/{thumbnail_filename}.jpg")
        yt_title, yt_description = generate_youtube(
            scoreboardNumber, display_phase, replace_characters=[["<", "❮"], [">", "❯"]])
        with open(f"{out_path}/{thumbnail_filename}_title.txt", "wt", encoding="utf-8") as txt_file:
            txt_file.write(yt_title)
        with open(f"{out_path}/{thumbnail_filename}_desc.txt", "wt", encoding="utf-8") as txt_file:
            txt_file.write(yt_description)
        logger.info(
            f"YouTube data successfully saved as {out_path}/{thumbnail_filename}_title.txt and {out_path}/{thumbnail_filename}_desc.txt")
        return f"{out_path}/{thumbnail_filename}.png"
    else:
        thumbnail_filename = f"template"
        thumbnail.save(f"{out_path}/{thumbnail_filename}.png")
        logger.info(
            f"Thumbnail successfully saved as {out_path}/{thumbnail_filename}.png")
        return f"{out_path}/{thumbnail_filename}.png"
