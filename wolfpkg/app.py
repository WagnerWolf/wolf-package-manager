"""Wolf Package Manager: repositórios, AUR, tray, terminal e diagnóstico."""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QAbstractItemView
)
from .backend import (Preferences, Package, STATE_DIR, upgrade_commands, installation_commands,
                      installation_batch_commands, lock_inspection, diagnostic_report,
                      missing_dependencies, space_snapshot)
from .terminal import TerminalRunner
from .worker import CheckWorker, SearchWorker
from .search_service import invalidate_search_cache

ASSETS = Path(__file__).resolve().parent / 'assets'
VERSION = '0.3.1'
STYLE = '''
QWidget {background:#121a25;color:#e8eff7;font-size:13px;}
QFrame#sidebar {background:#172231;border-right:1px solid #304058;}
QListWidget {background:transparent;border:none;padding:8px;outline:none;}
QListWidget::item {border-radius:9px;margin:3px;padding:13px;}
QListWidget::item:selected {background:#2a4668;color:white;}
QFrame#panel {border:1px solid #33445b;border-radius:12px;background:#1a2636;}
QFrame#panel QLabel {background:transparent;}
QPushButton {padding:10px 14px;background:#263a53;border:1px solid #405777;border-radius:8px;}
QPushButton:hover {background:#324d70;}
QPushButton:disabled {color:#7a8699;background:#1e2938;}
QPushButton#primary {background:#246aaf;border-color:#388acf;font-weight:bold;}
QTableWidget,QPlainTextEdit,QLineEdit,QSpinBox,QComboBox {
 background:#101823;color:#edf1fa;border:1px solid #35485d;border-radius:7px;}
QTableWidget {gridline-color:#29394c;}
QHeaderView::section {background:#253449;padding:9px;border:none;font-weight:bold;}
QLineEdit,QSpinBox,QComboBox {padding:9px;}
'''

def info(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet('color:#a6b8cc;')
    return lbl

def title(text):
    lbl = QLabel(text)
    lbl.setStyleSheet('font-size:23px;font-weight:bold;')
    return lbl

def tray_icon(state):
    # Usa o lobo verdadeiro, não o arco abstrato parecido com 'C'.
    canvas = QPixmap(64, 64)
    canvas.fill(Qt.GlobalColor.transparent)
    paint = QPainter(canvas)
    paint.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint.setBrush(QColor('#172333'))
    paint.setPen(QPen(QColor('#6d849e'), 2))
    paint.drawRoundedRect(2, 2, 60, 60, 14, 14)
    logo = QPixmap(str(ASSETS / 'wolf-tray.png'))
    if not logo.isNull():
        paint.drawPixmap(7, 5, 49, 49, logo)
    paint.setPen(QPen(QColor('#172333'), 3))
    paint.setBrush(QColor({'ok':'#43b887','updates':'#e2a83b', 'busy':'#428fd2',
                            'error':'#e36668'}.get(state,'#8395ac')))
    paint.drawEllipse(43, 42, 17, 17)
    paint.end()
    return QIcon(canvas)


class DiagnosticWorker(QThread):
    ready = Signal(str)
    def run(self):
        self.ready.emit(diagnostic_report())


class Manager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.prefs = Preferences.load()
        self.updates = []
        self.search_results = []
        self.checker = None
        self.searcher = None
        self.errors = {}
        self._last_announced = frozenset()
        self.running_plan = ()
        self.stage = -1
        self.failed_stage = -1
        self.plan_label = ''
        self.quitting = False
        self.operation_inactive_warned = False
        self._lock_warning_shown = False
        self.runner = TerminalRunner(self)
        self._pending_terminal = []
        self.runner.output.connect(self.queue_terminal)
        self.runner.finished.connect(self.finished_command)
        self._log_path = None
        self.setWindowTitle(f'Wolf Package Manager {VERSION}')
        self.setWindowIcon(QIcon(str(ASSETS / 'wolf-logo.png')))
        self.resize(1110, 720)
        self.setMinimumSize(860, 595)
        self.setStyleSheet(STYLE)
        self._build()
        self._setup_tray()
        self.output_timer = QTimer(self)
        self.output_timer.timeout.connect(self.flush_terminal)
        self.output_timer.start(120)
        self.interval_timer = QTimer(self)
        self.interval_timer.timeout.connect(self.check_updates)
        self.interval_timer.setInterval(max(15, self.prefs.interval_minutes) * 60_000)
        self.interval_timer.start()
        self.activity_timer = QTimer(self)
        self.activity_timer.timeout.connect(self.check_activity)
        self.activity_timer.start(15_000)
        QTimer.singleShot(1200, self.check_updates)

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        columns = QHBoxLayout(root)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        sidebar = QFrame(objectName='sidebar')
        sidebar.setFixedWidth(226)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 22, 10, 14)
        brand = QLabel('🐺 Wolf Packages')
        brand.setWordWrap(True)
        brand.setStyleSheet('font-size:18px;font-weight:bold;')
        side.addWidget(brand)
        side.addWidget(info('Gerenciador do Wolf Linux'))
        self.nav = QListWidget()
        self.nav.addItems(['Atualizações', 'Pesquisar e instalar', 'Terminal', 'Preferências', 'Sobre'])
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(lambda i: self.stack.setCurrentIndex(i) if i >= 0 else None)
        side.addWidget(self.nav, 1)
        self.sidebar_status = info('Aguardando verificação…')
        side.addWidget(self.sidebar_status)
        columns.addWidget(sidebar)
        self.stack = QStackedWidget()
        columns.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_updates())
        self.stack.addWidget(self._page_search())
        self.stack.addWidget(self._page_terminal())
        self.stack.addWidget(self._page_settings())
        self.stack.addWidget(self._page_about())

    def _page_updates(self):
        page = QWidget(); v = QVBoxLayout(page)
        v.setContentsMargins(23,23,23,20); v.setSpacing(13)
        v.addWidget(title('Atualizações disponíveis'))
        self.summary = info('Consultando repositórios e AUR…'); v.addWidget(self.summary)
        cards = QHBoxLayout()
        self.repo_count = QLabel('Repositórios: —')
        self.aur_count = QLabel('AUR: —')
        for lbl in (self.repo_count,self.aur_count):
            box = QFrame(objectName='panel'); l = QVBoxLayout(box)
            lbl.setStyleSheet('font-size:17px;font-weight:bold;'); l.addWidget(lbl)
            cards.addWidget(box)
        v.addLayout(cards)
        self.table = self._table(['Origem', 'Pacote', 'Instalada', 'Disponível'], stretch=1)
        v.addWidget(self.table,1)
        self.details = info('A instalação ou atualização só começa após sua confirmação.')
        v.addWidget(self.details)
        row = QHBoxLayout()
        self.btn_check = QPushButton('Verificar agora'); self.btn_check.clicked.connect(self.check_updates)
        self.btn_upgrade = QPushButton('Atualizar repositórios + AUR')
        self.btn_upgrade.setObjectName('primary'); self.btn_upgrade.clicked.connect(self.start_upgrade)
        row.addWidget(self.btn_check); row.addStretch(); row.addWidget(self.btn_upgrade)
        v.addLayout(row)
        return page

    def _page_search(self):
        page=QWidget(); v=QVBoxLayout(page)
        v.setContentsMargins(23,23,23,20); v.setSpacing(12)
        v.addWidget(title('Pesquisar e instalar pacotes'))
        v.addWidget(info('Pesquise nomes, descrições ou o nome de um comando, como nc. Marque vários pacotes para instalar juntos. AUR exige yay.'))
        row=QHBoxLayout()
        self.search_text=QLineEdit()
        self.search_text.setPlaceholderText('Pacote, descrição ou comando (ex.: nc)')
        self.search_text.returnPressed.connect(self.search_packages)
        self.search_scope=QComboBox()
        self.search_scope.addItems(['Todos','Repositório','AUR','Comandos'])
        self.search_scope.setMinimumWidth(145)
        self.btn_search=QPushButton('Pesquisar')
        self.btn_search.setObjectName('primary')
        self.btn_search.clicked.connect(self.search_packages)
        row.addWidget(self.search_text,1);row.addWidget(self.search_scope);row.addWidget(self.btn_search)
        v.addLayout(row)
        self.search_message=info('Digite o termo e clique em Pesquisar.')
        v.addWidget(self.search_message)
        self.results=self._table(['✓', 'Origem', 'Pacote', 'Versão', 'Descrição'], stretch=4)
        self.results.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        self.results.itemChanged.connect(self._selection_changed)
        v.addWidget(self.results,1)
        self.selected_package=info('Marque os pacotes desejados na primeira coluna.')
        v.addWidget(self.selected_package)
        row2=QHBoxLayout()
        self.btn_install=QPushButton('Instalar pacotes marcados')
        self.btn_install.setObjectName('primary')
        self.btn_install.setEnabled(False)
        self.btn_install.clicked.connect(self.install_selected)
        self.btn_mark_all=QPushButton('Marcar todos')
        self.btn_mark_all.clicked.connect(lambda: self._mark_results(True))
        self.btn_clear_marks=QPushButton('Desmarcar')
        self.btn_clear_marks.clicked.connect(lambda: self._mark_results(False))
        row2.addWidget(self.btn_mark_all);row2.addWidget(self.btn_clear_marks)
        row2.addStretch();row2.addWidget(self.btn_install)
        v.addLayout(row2)
        return page

    def _page_terminal(self):
        page=QWidget(); v=QVBoxLayout(page)
        v.setContentsMargins(23,23,23,20);v.setSpacing(10)
        v.addWidget(title('Terminal integrado'))
        v.addWidget(info('Saída em tempo real. Use o campo abaixo apenas para prompts excepcionais; o sudo usa uma janela de senha separada.'))
        self.console=QPlainTextEdit();self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(8000)
        font=QFont('monospace',10);font.setStyleHint(QFont.StyleHint.Monospace)
        self.console.setFont(font);v.addWidget(self.console,1)
        self.terminal_state=info('Nenhuma operação em andamento.')
        v.addWidget(self.terminal_state)
        input_row=QHBoxLayout()
        self.terminal_input=QLineEdit()
        self.terminal_input.setPlaceholderText('Resposta a um prompt, se necessário')
        self.terminal_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.terminal_input.setEnabled(False)
        self.terminal_input.returnPressed.connect(self.send_terminal_input)
        self.mask=QCheckBox('Ocultar entrada');self.mask.setChecked(True)
        self.mask.toggled.connect(lambda on: self.terminal_input.setEchoMode(
            QLineEdit.EchoMode.Password if on else QLineEdit.EchoMode.Normal))
        self.btn_send=QPushButton('Enviar');self.btn_send.setEnabled(False)
        self.btn_send.clicked.connect(self.send_terminal_input)
        input_row.addWidget(self.terminal_input,1)
        input_row.addWidget(self.mask);input_row.addWidget(self.btn_send)
        v.addLayout(input_row)
        buttons=QHBoxLayout()
        self.btn_retry=QPushButton('Repetir etapa com erro')
        self.btn_retry.setEnabled(False);self.btn_retry.clicked.connect(self.retry_failed)
        diag=QPushButton('Diagnóstico');diag.clicked.connect(self.show_diagnostics)
        lock_button=QPushButton('Analisar db.lck');lock_button.clicked.connect(self.show_lock_diagnostics)
        clear=QPushButton('Limpar visualização');clear.clicked.connect(self.console.clear)
        buttons.addWidget(self.btn_retry);buttons.addWidget(diag);buttons.addWidget(lock_button);buttons.addWidget(clear);buttons.addStretch()
        v.addLayout(buttons)
        return page

    def _page_settings(self):
        page=QWidget();v=QVBoxLayout(page)
        v.setContentsMargins(23,23,23,20);v.setSpacing(15)
        v.addWidget(title('Preferências'))
        v.addWidget(info('Intervalo de consulta automática em minutos. Nunca instala pacotes sem sua confirmação.'))
        self.interval=QSpinBox();self.interval.setRange(15,1440);self.interval.setSingleStep(15)
        self.interval.setValue(self.prefs.interval_minutes);self.interval.setSuffix(' min');v.addWidget(self.interval)
        self.notify_box=QCheckBox('Notificar sobre novas atualizações no tray')
        self.notify_box.setChecked(self.prefs.notifications);v.addWidget(self.notify_box)
        self.tray_box=QCheckBox('Fechar a janela mantendo monitoramento na bandeja')
        self.tray_box.setChecked(self.prefs.close_to_tray);v.addWidget(self.tray_box)
        self.auto_aur_box=QCheckBox('AUR: tentar instalação automática, sem prompts de revisão de PKGBUILD')
        self.auto_aur_box.setChecked(self.prefs.aur_automatic);v.addWidget(self.auto_aur_box)
        v.addWidget(info('Atenção: pacotes AUR são mantidos por terceiros e seus scripts podem executar código na máquina. Desmarque para manter as perguntas de revisão do yay. --noconfirm não garante ausência de todos os prompts nem elimina erros.'))
        save=QPushButton('Salvar preferências');save.setObjectName('primary');save.clicked.connect(self.save_prefs)
        v.addWidget(save);v.addStretch()
        return page

    def _page_about(self):
        page=QWidget();v=QVBoxLayout(page)
        v.setContentsMargins(23,23,23,20)
        v.addWidget(title('Wolf Package Manager '+VERSION))
        v.addWidget(info('Repositórios: pacman. Pacotes AUR: yay como usuário comum. Busca por comandos usa o índice repo.files do pacman, sem cobertura de arquivos de pacotes AUR.'))
        v.addWidget(info('Atualizações: pacman -Syu seguido de yay --aur, somente após sucesso da etapa anterior. Nunca atualizar repositórios isoladamente com pacman -Sy.'))
        v.addWidget(info('O monitor avisa quando uma etapa demora sem produzir saída e oferece diagnóstico. Não encerra pacman/makepkg automaticamente para evitar transações interrompidas.'))
        v.addWidget(info('Os logs são restritos ao usuário em ~/.local/state/wolf-package-manager/logs/. Não guarde informações sensíveis nos prompts do terminal.'))
        v.addStretch();return page

    def _table(self, headings, stretch):
        t=QTableWidget(0,len(headings))
        t.setHorizontalHeaderLabels(headings)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setSectionResizeMode(stretch,QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        return t

    def _setup_tray(self):
        self.tray=QSystemTrayIcon(tray_icon('busy'),self)
        menu=QMenu(self)
        menu.addAction('Abrir',self.show_window)
        menu.addAction('Pesquisar',lambda: self.open_page(1))
        menu.addAction('Verificar atualizações',self.check_updates)
        menu.addSeparator();menu.addAction('Sair',self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.setToolTip('Wolf Package Manager — verificando…')
        self.tray.show()

    def show_window(self):
        self.show();self.raise_();self.activateWindow()

    def open_page(self,index):
        self.nav.setCurrentRow(index)
        self.show_window()

    def _busy(self):
        diagnostic=getattr(self,'diagnostic_worker',None)
        return (self.runner.running() or (self.checker is not None and self.checker.isRunning())
                or (self.searcher is not None and self.searcher.isRunning())
                or (diagnostic is not None and diagnostic.isRunning()))

    def _set_actions_enabled(self, enable):
        self.btn_check.setEnabled(enable)
        self.btn_upgrade.setEnabled(enable)
        self.btn_search.setEnabled(enable)
        self.btn_install.setEnabled(enable and bool(self._marked_packages()))

    def save_prefs(self):
        self.prefs.interval_minutes=self.interval.value()
        self.prefs.notifications=self.notify_box.isChecked()
        self.prefs.close_to_tray=self.tray_box.isChecked()
        self.prefs.aur_automatic=self.auto_aur_box.isChecked()
        self.prefs.save()
        self.interval_timer.setInterval(max(15,self.prefs.interval_minutes)*60000)
        QMessageBox.information(self,'Preferências','Preferências salvas.')

    def check_updates(self):
        if self._busy():return
        self.tray.setIcon(tray_icon('busy'))
        self.summary.setText('Verificando repositórios e AUR…')
        self._set_actions_enabled(False)
        self.checker=CheckWorker(self)
        self.checker.done.connect(self.checked)
        self.checker.start()

    def checked(self,result,errors):
        self.errors=errors
        self.updates=[x for source in ('Repositório','AUR') for x in result.get(source,[])]
        self.table.setRowCount(len(self.updates))
        for row,p in enumerate(self.updates):
            for col,value in enumerate((p.source,p.name,p.installed,p.available)):
                self.table.setItem(row,col,QTableWidgetItem(value))
        counts={s:len(result.get(s,[])) for s in ('Repositório','AUR')}
        self.repo_count.setText('Repositórios: '+(str(counts['Repositório']) if 'Repositório' not in errors else 'Erro'))
        self.aur_count.setText('AUR: '+(str(counts['AUR']) if 'AUR' not in errors else 'Erro'))
        now=datetime.now().strftime('%d/%m/%Y %H:%M')
        total=len(self.updates)
        self.sidebar_status.setText(f'Última consulta: {now}\nAtualizações: {total}')
        self._set_actions_enabled(True)
        if errors:
            self.summary.setText('Consulta parcial: '+' | '.join(f'{k}: {v}' for k,v in errors.items()))
            self.tray.setIcon(tray_icon('error'))
        else:
            self.summary.setText(f'{total} atualização(ões) encontrada(s) — {now}')
            self.tray.setIcon(tray_icon('updates' if total else 'ok'))
            ids=frozenset((u.source,u.name,u.available) for u in self.updates)
            if (total and self.prefs.notifications and ids!=self._last_announced
                    and self.tray.isSystemTrayAvailable()):
                self.tray.showMessage('Wolf Linux — atualizações disponíveis',
                    f'{total} pacote(s): '+', '.join(x.name for x in self.updates[:4]),
                    QSystemTrayIcon.MessageIcon.Information,7000)
            self._last_announced=ids
        self.tray.setToolTip(f'Wolf Package Manager — {total} atualização(ões)'+(' (parcial)' if errors else ''))

    def search_packages(self):
        if self._busy():return
        query=self.search_text.text().strip()
        if len(query)<2:
            self.search_message.setText('Digite pelo menos 2 caracteres para pesquisar.')
            return
        self._set_actions_enabled(False)
        self.btn_install.setEnabled(False)
        self.search_message.setText('Pesquisando em paralelo…')
        self.searcher=SearchWorker(query,self.search_scope.currentText(),self)
        self.searcher.progress.connect(self.search_progress)
        self.searcher.done.connect(self.searched)
        self.searcher.start()

    def search_progress(self, source, completed, total, cached):
        suffix = ' (cache)' if cached else ''
        self.search_message.setText(
            f'Buscando: {completed}/{total} fonte(s) consultada(s). '
            f'Última: {source}{suffix}.')

    def searched(self, found, errors):
        # Ordena pacotes exatos primeiro; depois origem e nome.
        q=self.search_text.text().strip().lower()
        # Merge filename and package matches; do not install the same package twice.
        merged={}
        for pkg in found:
            key=(pkg.source,pkg.repo,pkg.name)
            if key not in merged or 'Fornece o comando' in pkg.description:
                merged[key]=pkg
        self.search_results=sorted(merged.values(),key=lambda p:(
            p.name!=q, p.name.lower(),
            0 if p.repo in ('core','extra','multilib','core-testing','extra-testing')
            else (1 if p.source=='Repositório' else 2), p.repo))
        # Preserve user intent only within one search. No stale selection from previous results.
        self.results.blockSignals(True)
        self.results.setRowCount(len(self.search_results))
        for row,p in enumerate(self.search_results):
            mark=QTableWidgetItem('')
            mark.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            mark.setCheckState(Qt.CheckState.Unchecked)
            self.results.setItem(row,0,mark)
            for col,value in enumerate((p.repo.upper() if p.source=='AUR' else p.repo, p.name, p.version,p.description),start=1):
                self.results.setItem(row,col,QTableWidgetItem(value))
        self.results.blockSignals(False)
        self.selected_package.setText('Marque os pacotes desejados para instalar.')
        self._set_actions_enabled(True)
        msg=f'{len(self.search_results)} resultado(s).'
        if errors:msg+=' Consulta parcial: '+' | '.join(f'{k}: {v}' for k,v in errors.items())
        self.search_message.setText(msg)

    def _marked_packages(self):
        chosen=[]
        for row, p in enumerate(self.search_results):
            item=self.results.item(row,0)
            if item is not None and item.checkState()==Qt.CheckState.Checked:
                chosen.append(p)
        return chosen

    def _mark_results(self,checked):
        if self._busy():return
        self.results.blockSignals(True)
        for row in range(self.results.rowCount()):
            item=self.results.item(row,0)
            if item:item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.results.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self,*_):
        count=len(self._marked_packages())
        self.selected_package.setText(f'{count} pacote(s) marcado(s). '
                                      'Um único pacman -Syu, seguido pelo yay para os AUR.')
        self.btn_install.setEnabled(bool(count) and not self._busy())

    def _confirm(self, heading, explanation, aur_involved=False):
        msg=explanation+'\n\n'+space_snapshot()
        if aur_involved and self.prefs.aur_automatic:
            msg+='\n\nAUR automático: scripts de terceiros serão executados SEM revisão de PKGBUILD. Deseja continuar?'
        return QMessageBox.question(self,heading,msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes

    def install_selected(self):
        if self._busy():return
        packages=self._marked_packages()
        if not packages:return
        try:cmds=installation_batch_commands(packages,self.prefs.aur_automatic)
        except ValueError as exc:
            QMessageBox.warning(self,'Pacote inválido',str(exc));return
        names=', '.join(f'{p.repo}/{p.name}' for p in packages[:15])
        if len(packages)>15:names+=f' ... (+{len(packages)-15})'
        if not self._confirm('Confirmar instalação',
                f'Instalar {len(packages)} pacote(s)?\n\n{names}\n\n'
                'O pacman atualizará os repositórios primeiro. O yay instalará os pacotes AUR somente depois.',
                any(p.source=='AUR' for p in packages)):
            return
        self._begin_plan(cmds,f'Instalação de {len(packages)} pacote(s)')

    def start_upgrade(self):
        if self._busy():return
        missing=missing_dependencies()
        if missing:
            QMessageBox.warning(self,'Dependências ausentes','Instale: '+', '.join(missing));return
        if not self._confirm('Confirmar atualização',
                'Atualizar repositórios (Arch/Chaotic-AUR) e, somente após sucesso, os pacotes AUR com yay?',True):
            return
        self._begin_plan(upgrade_commands(self.prefs.aur_automatic),'Atualização completa')

    def _begin_plan(self, commands, label):
        state, explanation = lock_inspection()
        if state != 'livre':
            QMessageBox.warning(self, 'Banco de pacotes bloqueado',
                explanation+'\n\nNenhuma instalação será iniciada enquanto db.lck estiver presente.')
            self.open_page(2)
            self.append_terminal('\n[LOCK] '+explanation+'\n')
            return
        self.running_plan=tuple(commands)
        self.plan_label=label
        self.failed_stage=-1
        self.btn_retry.setEnabled(False)
        self._new_log()
        self.open_page(2)
        self.append_terminal('\n======== '+label.upper()+' ========\n'+space_snapshot()+'\n')
        self._run_stage(0)

    def _run_stage(self,index):
        self.stage=index
        cmd=self.running_plan[index]
        self._set_actions_enabled(False)
        self.btn_retry.setEnabled(False)
        self.operation_inactive_warned=False
        self.append_terminal('\n--- Etapa '+str(index+1)+'/'+str(len(self.running_plan))+' ---\n$ '+
            ' '.join(cmd)+'\n')
        self.terminal_state.setText(f'Executando etapa {index+1}/{len(self.running_plan)}…')
        self.tray.setIcon(tray_icon('busy'))
        try:
            self.runner.launch(cmd)
            self.terminal_input.setEnabled(True)
            self.btn_send.setEnabled(True)
        except Exception as exc:
            self.append_terminal(f'Falha ao iniciar: {exc}\n')
            self.finished_command(127)

    def finished_command(self,exit_code):
        # Mesmo uma etapa com erro pode ter atualizado pacotes antes da falha.
        invalidate_search_cache()
        self.flush_terminal()
        self.terminal_input.clear()
        self.terminal_input.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.append_terminal(f'\n[Etapa {self.stage+1}] código de saída: {exit_code}\n')
        if exit_code==0 and self.stage+1<len(self.running_plan):
            state, detail = lock_inspection()
            if state != 'livre':
                self.append_terminal('\n[ALERTA] Pacman retornou 0, mas db.lck persiste. '
                                     'Etapas posteriores NÃO serão executadas.\n'+detail+'\n')
                exit_code = 113
            else:
                self._run_stage(self.stage+1)
                return
        self._set_actions_enabled(True)
        if exit_code==0 and lock_inspection()[0] != 'livre':
            self.append_terminal('\n[ALERTA] A transação retornou 0, mas db.lck existe. '
                                 'A operação requer conferência antes de ser considerada concluída.\n')
            exit_code=113
        if exit_code==0:
            self.terminal_state.setText('Operação concluída. Reinicie caso tenham sido atualizados kernel ou bibliotecas críticas.')
            self.append_terminal('\n[OK] '+self.plan_label+' concluída.\n')
            self.tray.setIcon(tray_icon('ok'))
            self.btn_retry.setEnabled(False)
            self.failed_stage=-1
            QTimer.singleShot(1800,self.check_updates)
        else:
            self.terminal_state.setText('Falha. Nenhuma etapa posterior será executada. Consulte o log e o diagnóstico.')
            self.append_terminal('\n[ERRO] Operação interrompida. Corrija a causa antes de repetir.\n')
            self.failed_stage=self.stage
            self.btn_retry.setEnabled(True)
            self.tray.setIcon(tray_icon('error'))

    def retry_failed(self):
        if self._busy() or self.failed_stage<0:return
        if QMessageBox.question(self,'Repetir etapa',
            f'Repetir somente a etapa {self.failed_stage+1} de {len(self.running_plan)}?\n\n'+space_snapshot(),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self._run_stage(self.failed_stage)

    def _new_log(self):
        folder=STATE_DIR/'logs'
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(folder,0o700)
        # Rotação simples, evitando acumular logs por tempo indeterminado.
        files=sorted(folder.glob('operation-*.log'))
        for f in files[:-12]:
            try:f.unlink()
            except OSError:pass
        self._log_path=folder/('operation-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.log')
        fd=os.open(self._log_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(fd)
        self.append_terminal('\n[Log] '+str(self._log_path)+'\n')

    def queue_terminal(self, value):
        # Agrupa rajadas de saída; evita congelamento da GUI com stdout volumoso.
        self._pending_terminal.append(value)

    def flush_terminal(self):
        if self._pending_terminal:
            text = ''.join(self._pending_terminal)
            self._pending_terminal.clear()
            self.append_terminal(text)

    def append_terminal(self,value):
        cur=self.console.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(value)
        self.console.setTextCursor(cur)
        self.console.ensureCursorVisible()
        if self._log_path:
            try:
                with open(self._log_path,'a',encoding='utf-8') as f:
                    f.write(value)
            except OSError:pass

    def send_terminal_input(self):
        value=self.terminal_input.text()
        self.terminal_input.clear()
        if not self.runner.running():return
        try:self.runner.send_line(value)
        except ValueError as exc:self.append_terminal('\nEntrada recusada: '+str(exc)+'\n')

    def check_activity(self):
        if not self.runner.running():return
        quiet=int(monotonic()-self.runner.last_output)
        if quiet>=90:
            self.terminal_state.setText(f'Processo ativo, sem nova saída há {quiet}s. Consulte Diagnóstico. NÃO interrompa o pacman à força.')
            if not self.operation_inactive_warned:
                self.operation_inactive_warned=True
                self.append_terminal(f'\n[AVISO] Sem saída há {quiet}s. Pode estar compilando, baixando ou aguardando I/O. Veja Diagnóstico.\n')
                if self.prefs.notifications and self.tray.isSystemTrayAvailable():
                    self.tray.showMessage('Wolf Packages — sem progresso visível',
                        'A atualização ainda está ativa. Abra o terminal e use Diagnóstico.',
                        QSystemTrayIcon.MessageIcon.Warning,6500)
        elif self.operation_inactive_warned:
            self.operation_inactive_warned=False
            self.terminal_state.setText('Processo voltou a produzir saída.')

    def show_lock_diagnostics(self):
        state, details = lock_inspection()
        QMessageBox.information(self,'Estado da trava do pacman',details+'\n\n'
            'A limpeza automática do db.lck não é oferecida: podem existir hooks ainda em execução.')
        self.append_terminal('\n[Análise db.lck: '+state+']\n'+details+'\n')
        self.open_page(2)

    def show_diagnostics(self):
        if getattr(self,'diagnostic_worker',None) is not None and self.diagnostic_worker.isRunning():
            return
        self.append_terminal('\n[Diagnóstico] Coletando informações de pacman, kernel, NVIDIA e DKMS…\n')
        self.open_page(2)
        self.diagnostic_worker=DiagnosticWorker(self)
        self.diagnostic_worker.ready.connect(lambda content: self.append_terminal('\n'+content+'\n'))
        self.diagnostic_worker.start()

    def closeEvent(self,event):
        if not self.quitting and self.prefs.close_to_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide();event.ignore();return
        if self._busy():
            event.ignore();QMessageBox.warning(self,'Operação em andamento','Aguarde a conclusão para fechar.')
            return
        event.accept()
        if not QSystemTrayIcon.isSystemTrayAvailable():QApplication.instance().quit()

    def quit_app(self):
        if self._busy():
            self.show_window()
            QMessageBox.warning(self,'Operação em andamento','Não é seguro sair durante uma instalação ou atualização.')
            return
        self.quitting=True;QApplication.instance().quit()


def main():
    if not sys.platform.startswith('linux'):
        raise SystemExit('Disponível apenas para Linux.')
    if os.geteuid()==0:
        raise SystemExit('Execute como usuário comum, nunca como root.')
    app=QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    start_in_tray = '--tray' in sys.argv or '--minimized' in sys.argv
    # At login KDE may create its status notifier after this application starts.
    # Wait briefly for it rather than flashing the main window on the desktop.
    name=f'wolf-package-manager-{os.getuid()}'
    client=QLocalSocket();client.connectToServer(name)
    if client.waitForConnected(350):
        # An autostart attempt must never pop open the already-running window.
        client.write(b'PING\n' if start_in_tray else b'SHOW\n')
        client.waitForBytesWritten(350);return
    server=QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        raise SystemExit('Não foi possível abrir o socket local (outra instância ativa ou socket antigo).')
    window=Manager()
    def client_connected():
        while server.hasPendingConnections():
            peer=server.nextPendingConnection()
            def read_command(sock=peer):
                message=bytes(sock.readAll()).strip()
                if message == b'SHOW':
                    window.show_window()
                sock.disconnectFromServer()
                sock.deleteLater()
            peer.readyRead.connect(read_command)
    server.newConnection.connect(client_connected)
    if start_in_tray:
        def ensure_tray():
            if not QSystemTrayIcon.isSystemTrayAvailable():
                window.show_window()  # avoid a permanently invisible app
        QTimer.singleShot(20_000, ensure_tray)
    else:
        window.show()
    return app.exec()
