from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
import os
import traceback
import json
from loguru import logger


class AutonaAlertNotificationSignals(QObject):
    alerts = Signal(object)


class AutonaAlertNotification(QObject):
    instance: "AutonaAlertNotification" = None

    def __init__(self) -> None:
        super().__init__()
        # MarkNotificationRed() reads ./user_data/alerts_red.json before
        # appending to it, so the notification object guarantees its own data
        # file. This used to sit at module level next to the singleton
        # construction; it belongs to __init__ rather than to bootstrap so
        # that the invariant travels with the object that depends on it.
        os.makedirs("./user_data", exist_ok=True)
        if not os.path.exists("./user_data/alerts_red.json"):
            with open("./user_data/alerts_red.json", 'w') as outfile:
                outfile.write("[]")

        self.signals = AutonaAlertNotificationSignals()
        self.signals.alerts.connect(self.ShowAlerts)

    def UiMounted(self):
        # Upstream TSH fetched alerts.json from joaorb64/Autona. We don't
        # host an equivalent file, so the remote fetch is disabled. The
        # class is kept so existing signal wiring stays valid.
        return

    def ShowAlerts(self, alerts):
        i = 1

        for alertId, alert in alerts.items():
            message = QDialog()
            message.setWindowModality(
                Qt.WindowModality.ApplicationModal)
            vbox = QVBoxLayout()
            message.setLayout(vbox)
            message.setWindowTitle(
                QApplication.translate("app", "Notifications ({0}/{1})").format(i, len(alerts.keys())))
            message.layout().addWidget(QLabel(alert.get("alert")))

            hbox = QHBoxLayout()
            vbox.addLayout(hbox)

            btOk = QPushButton("OK")
            hbox.addWidget(btOk)
            btRemindLater = QPushButton(QApplication.translate("app", "Remind later"))
            hbox.addWidget(btRemindLater)

            message.setMinimumWidth(500)
            message.setMinimumHeight(200)

            btOk.clicked.connect(
                lambda: self.MarkNotificationRed(alertId))
            btOk.clicked.connect(
                lambda: message.close())
            btRemindLater.clicked.connect(
                lambda: message.close())

            message.exec()

            i += 1

    def MarkNotificationRed(self, id):
        alerts_red = None

        try:
            alerts_red = json.load(
                open('./user_data/alerts_red.json', encoding='utf-8'))
        except Exception as e:
            logger.error(traceback.format_exc())

        if alerts_red is not None:
            alerts_red.append(id)
            with open("./user_data/alerts_red.json", 'w') as outfile:
                json.dump(alerts_red, outfile)
