from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
import os
import json
from loguru import logger
import glob

from pathlib import Path


class AutonaControllerHelperSignals(QObject):
    controllersUpdated = Signal()


class AutonaControllerHelper(QObject):
    instance: "AutonaControllerHelper" = None

    signals = AutonaControllerHelperSignals()

    def __init__(self) -> None:
        super().__init__()
        self.controller_list = {}
        self.controllerModel = QStandardItemModel()

    def init(self):
        self.BuildControllerTree()
        self.UpdateControllerModel()

    def BuildControllerTree(self):
        controller_list = {}
        list_controller_directories = glob.glob("./assets/controller/ControllerDatabase-main/*/*/*/")
        if os.name == "nt":
            for i in range(len(list_controller_directories)):
                list_controller_directories[i] = list_controller_directories[i].replace("\\\\", "/")
                list_controller_directories[i] = list_controller_directories[i].replace("\\", "/")
        for controller_directory in list_controller_directories:
            if os.path.exists(f"{controller_directory}/config.json"):
                split = controller_directory.split("/")
                controller_id = f"{split[-4]}/{split[-3]}/{split[-2]}"
                # print(f"Loading: {controller_id}")
                with open(f"{controller_directory}/config.json", "rt", encoding="utf-8") as config_file:
                    config_json = json.loads(config_file.read())
                    if os.path.exists(f'{"/".join(split[:-2])}/config.json'):
                        with open(f'{"/".join(split[:-2])}/config.json', "rt", encoding="utf-8") as manufacturer_file:
                            manufacturer = json.loads(manufacturer_file.read()).get("name")
                            # print(f"Manufacturer: {manufacturer}")
                    else:
                        manufacturer = None

                    if os.path.exists(f'{"/".join(split[:-3])}/config.json'):
                        with open(f'{"/".join(split[:-3])}/config.json', "rt", encoding="utf-8") as controller_type_file:
                            controller_type = json.loads(controller_type_file.read()).get("name")
                            # print(f"Type: {controller_type}")
                    else:
                        controller_type = None

                    if os.path.exists(f"{controller_directory}/image.png"):
                        icon_path = f"{controller_directory}/image.png"
                    else:
                        icon_path = None
                        
                    if os.path.exists(f"{controller_directory}/icon.png"):
                        simple_icon_path = f"{controller_directory}/icon.png"
                    else:
                        simple_icon_path = None

                    # Get category icon
                    category_path = Path(controller_directory).parent.parent.relative_to("./")
                    if os.path.exists(f"./{category_path}/icon.png"):
                        category_icon_path = f"./{category_path}/icon.png"
                    else:
                        category_icon_path = None

                    controller_json = {
                        "name": config_json.get("name"),
                        "manufacturer": manufacturer,
                        "type": controller_type,
                        "icon_path": icon_path,
                        "config_path": f"{controller_directory}/config.json",
                        "simple_icon_path": simple_icon_path,
                        "category_icon_path": category_icon_path
                    }

                    if config_json.get("short_name"):
                        controller_json["short_name"] = config_json.get("short_name")
                    else:
                        controller_json["short_name"] = config_json.get("name")

                    controller_list[controller_id] = controller_json
        self.controller_list = controller_list


    def UpdateControllerModel(self):
        try:
            self.controllerModel = QStandardItemModel()

            # Add one empty
            item = QStandardItem("")
            self.controllerModel.appendRow(item)

            for c in self.controller_list.keys():
                item = QStandardItem()
                item.setData(c, Qt.ItemDataRole.EditRole)
                data = {
                    "name": self.controller_list[c].get("name"),
                    "short_name": self.controller_list[c].get("short_name"),
                    "manufacturer": self.controller_list[c].get("manufacturer"),
                    "type": self.controller_list[c].get("type"),
                    "codename": c,
                    "icon_path": self.controller_list[c].get("icon_path"),
                    "simple_icon_path": self.controller_list[c].get(
                        "simple_icon_path"),
                    "category_icon_path": self.controller_list[c].get(
                        "category_icon_path"),
                }

                # Use QIcon(path) directly: it stores the path and defers
                # decoding to first paint, unlike `QIcon(QPixmap.fromImage(
                # QImage(path)))` which eagerly decoded the PNG at startup.
                # Previously that, combined with three extra QImage loads
                # per controller just to compute unused dimension dicts,
                # burned ~4 seconds across 38 controllers.
                icon_path = data["icon_path"] or "./assets/icons/cancel.svg"
                item.setIcon(QIcon(icon_path))

                if self.controller_list[c].get("name") != c:
                    item.setData(
                        f'{self.controller_list[c].get("name")}',
                        Qt.ItemDataRole.EditRole)

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.controllerModel.appendRow(item)

            self.controllerModel.sort(0)
        except:
            logger.error(traceback.format_exc())

AutonaControllerHelper.instance = AutonaControllerHelper()
