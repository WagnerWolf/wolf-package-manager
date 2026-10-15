"""Prompt gráfico efêmero para sudo -A; nunca registra nem compartilha a senha."""
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QDialog

def main():
    app = QApplication(sys.argv)
    prompt = sys.argv[1] if len(sys.argv) > 1 else 'Senha sudo:'
    dialog = QInputDialog()
    dialog.setWindowTitle('Wolf Linux — autenticação')
    dialog.setLabelText('Autenticação administrativa necessária\n' + prompt[:200])
    dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return 1
    # Somente stdout para o sudo: a senha NÃO passa pelo terminal do app.
    sys.stdout.write(dialog.textValue() + '\n')
    sys.stdout.flush()
    return 0

if __name__ == '__main__':
    sys.exit(main())
