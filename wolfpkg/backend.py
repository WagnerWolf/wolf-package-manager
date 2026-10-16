"""Backend puro (sem Qt): consulta, planos e limites do gerenciador Wolf."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'wolf-package-manager'
CONFIG_FILE = CONFIG_DIR / 'config.json'
STATE_DIR = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'wolf-package-manager'
PKG_NAME = re.compile(r'^[a-z0-9@._+\-]{1,128}$')
REPO_NAME = re.compile(r'^[a-z0-9._+\-]{1,64}$')

@dataclass(frozen=True)
class Update:
    source: str
    name: str
    installed: str
    available: str

@dataclass(frozen=True)
class Package:
    source: str  # 'Repositório' ou 'AUR'
    repo: str    # core, extra, chaotic-aur, aur, ...
    name: str
    version: str
    description: str
    installed: bool = False

@dataclass
class Preferences:
    interval_minutes: int = 120
    notifications: bool = True
    close_to_tray: bool = True
    aur_automatic: bool = True

    @classmethod
    def load(cls, path: Path = CONFIG_FILE):
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            interval = int(raw.get('interval_minutes', 120))
            return cls(max(15, min(1440, interval)),
                       bool(raw.get('notifications', True)),
                       bool(raw.get('close_to_tray', True)),
                       bool(raw.get('aur_automatic', True)))
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path = CONFIG_FILE):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd, tmp_name = tempfile.mkstemp(prefix='.config-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

class CheckError(Exception):
    pass


def parse_updates(output: str, source: str) -> list[Update]:
    found = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 4 and parts[2] == '->':
            found.append(Update(source, parts[0], parts[1], parts[3]))
    return found


def _query(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    if not shutil.which(argv[0]):
        raise CheckError(f'Comando ausente: {argv[0]}')
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, check=False,
                              env={**os.environ, 'LC_ALL': 'C', 'NO_COLOR': '1'})
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f'Tempo limite excedido em {argv[0]} ({timeout}s)') from exc
    except OSError as exc:
        raise CheckError(f'Falha ao iniciar {argv[0]}: {exc}') from exc


def check_repository_updates() -> list[Update]:
    """Consulta base isolada e detecta divergência com a base local do pacman.

    Não executa `pacman -Sy`: atualizar apenas o índice principal causaria
    risco de partial upgrade no Arch. Uma divergência é reportada como erro,
    nunca como falso "nenhuma atualização".
    """
    result = _query(['checkupdates', '--nocolor'], 120)
    if result.returncode not in (0, 2) or (result.returncode == 2 and result.stdout.strip()):
        raise CheckError(f'checkupdates retornou {result.returncode}: {result.stderr.strip() or result.stdout.strip()}')
    found = parse_updates(result.stdout, 'Repositório')
    if found:
        return found
    # Uma base temporária sem novidades pode divergir da base local que
    # `pacman -Syu` atualizou recentemente. Nessa condição, não afirmar zero.
    local = _query(['pacman', '-Qu'], 20)
    if local.returncode not in (0, 1):
        raise CheckError('Não foi possível conferir a base local do pacman: '
                         + (local.stderr.strip() or f'código {local.returncode}'))
    pending_local = parse_updates(local.stdout, 'Repositório')
    if pending_local:
        names = ', '.join(item.name for item in pending_local[:5])
        raise CheckError('Bases de atualizações divergentes: checkupdates não encontrou pacotes, '
                         f'mas pacman -Qu lista {len(pending_local)} ({names}). '
                         'Tente verificar novamente e confira seus espelhos.')
    return []


def check_aur_updates() -> list[Update]:
    result = _query(['yay', '-Qua', '--aur'], 120)
    if result.returncode != 0:
        if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
            return []
        raise CheckError(f'yay retornou {result.returncode}: {result.stderr.strip() or result.stdout.strip()}')
    return parse_updates(result.stdout, 'AUR')


def transaction_snapshot(log: Path = Path('/var/log/pacman.log'),
                         lock: Path = Path('/var/lib/pacman/db.lck')) -> tuple[bool, int | None, int | None]:
    """Instantâneo sem escrita. Detecta atualizações externas durante a consulta.

    Não substitui as travas de transação do pacman; apenas evita divulgar
    resultados coletados antes de uma instalação executada em outro terminal.
    """
    locked = lock.exists()
    try:
        st = log.stat()
        return locked, st.st_mtime_ns, st.st_size
    except OSError:
        return locked, None, None


def check_all_updates(*,
                      snapshot=transaction_snapshot,
                      repo_check=check_repository_updates,
                      aur_check=check_aur_updates) -> tuple[dict[str, list[Update]], dict[str, str]]:
    before = snapshot()
    if before[0]:
        return {}, {'Transação': 'Pacman em uso (db.lck presente). Verificação adiada.'}
    result: dict[str, list[Update]] = {}
    errors: dict[str, str] = {}
    for key, callback in (('Repositório', repo_check), ('AUR', aur_check)):
        try:
            result[key] = callback()
        except Exception as exc:
            errors[key] = str(exc)
    after = snapshot()
    if after[0] or after != before:
        return {}, {'Transação': 'O estado do pacman mudou durante a consulta. '
                                 'Resultados descartados para evitar notificação antiga.'}
    return result, errors


def validate_package(package: Package) -> None:
    if not PKG_NAME.fullmatch(package.name) or package.name.startswith(('-', '.')):
        raise ValueError('Nome de pacote inválido')
    if package.source not in ('AUR', 'Repositório'):
        raise ValueError('Origem inválida')
    if package.source == 'Repositório' and not REPO_NAME.fullmatch(package.repo):
        raise ValueError('Repositório inválido')
    if package.source == 'AUR' and package.repo != 'aur':
        raise ValueError('Origem AUR inválida')


def parse_pacman_search(text: str) -> list[Package]:
    """pacman -Ss: duas linhas por pacote; primeira é repo/nome versão [instalado]."""
    found = []
    for line in text.splitlines():
        if not line.startswith((' ', '\t')):
            parts = line.strip().split()
            if len(parts) >= 2 and '/' in parts[0]:
                repo, name = parts[0].split('/', 1)
                if REPO_NAME.fullmatch(repo) and PKG_NAME.fullmatch(name):
                    found.append(Package('Repositório', repo, name, parts[1], '',
                                         any('installed' in p.lower() or 'instalado' in p.lower() for p in parts[2:])))
        elif found and not found[-1].description:
            old = found[-1]
            found[-1] = Package(old.source, old.repo, old.name, old.version, line.strip(), old.installed)
    return found


def parse_yay_search(text: str) -> list[Package]:
    """yay -Ss --aur: aur/nome versão (votos) (+popularidade) ..."""
    results = []
    for p in parse_pacman_search(text):
        if p.repo.lower() == 'aur':
            results.append(Package('AUR', 'aur', p.name, p.version, p.description, p.installed))
    return results


def _normal_query(query: str) -> str:
    q = query.strip()
    if len(q) < 2 or len(q) > 90 or any(ord(ch) < 32 for ch in query):
        raise ValueError('Pesquise pelo menos 2 caracteres (máximo 90).')
    return q


def search_repositories(query: str) -> list[Package]:
    q = _normal_query(query)
    r = _query(['pacman', '-Ss', '--', q], 30)
    if r.returncode == 1 and not r.stdout.strip() and not r.stderr.strip():
        return []
    if r.returncode != 0:
        raise CheckError(f'pacman -Ss: {r.stderr.strip() or "falha de pesquisa"}')
    return parse_pacman_search(r.stdout)[:120]


def search_aur(query: str) -> list[Package]:
    q = _normal_query(query)
    r = _query(['yay', '-Ss', '--aur', '--color', 'never', '--', q], 60)
    if r.returncode == 1 and not r.stdout.strip() and not r.stderr.strip():
        return []
    if r.returncode != 0:
        raise CheckError(f'yay -Ss --aur: {r.stderr.strip() or "falha de pesquisa"}')
    return parse_yay_search(r.stdout)[:120]


def aur_flags(automatic: bool = True) -> tuple[str, ...]:
    # sudo interno do yay emprega askpass. O yay continua como usuário comum.
    flags = ('--sudoflags=-A',)
    if automatic:
        flags += ('--noconfirm', '--answerclean', 'None', '--answerdiff', 'None',
                  '--answeredit', 'None', '--noremovemake')
    return flags


def upgrade_commands(automatic_aur: bool = True) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """NUNCA ignorar falha do pacman antes de avançar para o yay."""
    return (('sudo', '-A', 'pacman', '-Syu', '--noconfirm'),
            ('yay', '-Sua', '--aur') + aur_flags(automatic_aur))


def installation_commands(p: Package, automatic_aur: bool = True) -> tuple[tuple[str, ...], ...]:
    """Usa -Syu no repositório para não fazer atualização parcial."""
    validate_package(p)
    if p.source == 'Repositório':
        return (('sudo', '-A', 'pacman', '-Syu', '--needed', '--noconfirm', f'{p.repo}/{p.name}'),)
    return (('sudo', '-A', 'pacman', '-Syu', '--noconfirm'),
            ('yay', '-S', '--aur', '--needed', p.name) + aur_flags(automatic_aur))


def parse_file_matches(data: str, requested: str) -> list[Package]:
    """Parse `pacman -F --machinereadable` repository\0package\0version\0path.

    Returns repo packages only. AUR cannot be indexed through the repo file database.
    """
    found: dict[tuple[str, str], Package] = {}
    for line in data.splitlines():
        fields = line.split('\0')
        if len(fields) != 4:
            continue
        repo, name, version, filename = fields
        if not REPO_NAME.fullmatch(repo) or not PKG_NAME.fullmatch(name):
            continue
        if Path(filename).name != requested:
            continue
        # Exclude documentation and non-executable files matching a basename.
        if not (filename.lstrip('/').startswith(('usr/bin/', 'bin/', 'usr/sbin/', 'sbin/'))):
            continue
        found[(repo, name)] = Package('Repositório', repo, name, version,
                                     f'Fornece o comando {requested}: {filename}')
    return list(found.values())[:100]


def search_command(name: str) -> list[Package]:
    term = name.strip()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.+\-]{1,63}', term):
        raise ValueError('Comando inválido (2 a 64 caracteres, sem caminhos).')
    r = _query(['pacman', '-F', '--machinereadable', '--', term], 30)
    if r.returncode == 1 and not r.stdout.strip() and not r.stderr.strip():
        return []
    if r.returncode != 0:
        raise CheckError('Índice de arquivos indisponível. Para habilitar busca de comandos, '
                         'execute uma vez: sudo pacman -Fy. Detalhe: '+
                         (r.stderr.strip() or f'pacman -F retornou {r.returncode}'))
    return parse_file_matches(r.stdout, term)


def installation_batch_commands(packages: list[Package], automatic_aur: bool = True) -> tuple[tuple[str, ...], ...]:
    """One pacman -Syu for repo packages, then one yay call for AUR packages."""
    if not packages:
        raise ValueError('Selecione pelo menos um pacote.')
    if len(packages) > 150:
        raise ValueError('Máximo de 150 pacotes por operação.')
    seen = set()
    repos, aur = [], []
    for p in packages:
        validate_package(p)
        key = (p.source, p.repo, p.name)
        if key in seen:
            continue
        seen.add(key)
        if p.source == 'AUR':
            aur.append(p.name)
        else:
            repos.append(f'{p.repo}/{p.name}')
    # AUR-only installs still need a full repo upgrade, avoiding partial upgrades.
    repo_cmd = ('sudo', '-A', 'pacman', '-Syu', '--needed', '--noconfirm') + tuple(repos)
    if not aur:
        return (repo_cmd,)
    return (repo_cmd, ('yay', '-S', '--aur', '--needed') + tuple(aur) + aur_flags(automatic_aur))


def lock_inspection(lock: Path = Path('/var/lib/pacman/db.lck')) -> tuple[str, str]:
    """Conservative, read-only lock assessment. Never proves safe to delete.

    The user must independently verify process state before privileged removal.
    """
    if not lock.exists():
        return ('livre', 'Trava do pacman ausente.')
    details = ['Trava presente: '+str(lock)]
    try:
        age = max(0, time.time() - lock.stat().st_mtime)
        details.append(f'Idade aproximada: {age/60:.0f} min (idade não prova obsolescência).')
    except OSError:
        pass
    # Even a successful unprivileged scan may miss privileged or unrelated owners.
    active = []
    try:
        result = subprocess.run(['ps', '-eo', 'pid=,comm='], capture_output=True,
                                text=True, timeout=5, check=False)
        if result.returncode != 0:
            details.append('Não foi possível consultar todos os processos.')
            return 'indeterminado', '\n'.join(details)
        watched = {'pacman', 'yay', 'paru', 'makepkg', 'pamac', 'octopi', 'dkms',
                   'mkinitcpio', 'depmod', 'dracut', 'paccache', 'pacman-key'}
        for line in result.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[1].lower() in watched:
                active.append(f'{parts[0]} {parts[1]}')
    except (OSError, subprocess.TimeoutExpired):
        return 'indeterminado', '\n'.join(details+['Não foi possível verificar os processos.'])
    if active:
        details.append('Processos relacionados ativos: '+', '.join(active[:20]))
        details.append('Não remova db.lck durante atividades de pacotes.')
        return 'ocupado', '\n'.join(details)
    if not shutil.which('fuser'):
        details.append('fuser indisponível; verificação incompleta.')
        return 'indeterminado', '\n'.join(details)
    try:
        f = subprocess.run(['fuser', str(lock)], capture_output=True,
                           text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 'indeterminado', '\n'.join(details+['Falha ao executar fuser.'])
    if f.returncode == 0:
        return 'ocupado', '\n'.join(details+['fuser encontrou utilizadores: '+f.stdout.strip(),
                                           'NÃO remova a trava.'])
    if f.returncode != 1:
        return 'indeterminado', '\n'.join(details+['fuser inconclusivo.'])
    return 'possivelmente_obsoleto', '\n'.join(details+[
        'Nenhum processo relacionado foi visto e fuser não encontrou um proprietário.',
        'Isso NÃO garante segurança: não há remoção automática. Confirme que nenhuma',
        'transação ou hook está em execução antes de qualquer intervenção manual.'])


def _diag_command(argv: list[str], seconds: int = 5) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, check=False)
        content = (r.stdout or r.stderr).strip()
        return content[:4000] if content else f'(sem saída; código {r.returncode})'
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'Indisponível: {exc}'


def diagnostic_report() -> str:
    """Read-only snapshot for previous kernel/NVIDIA update interruptions."""
    status, reason = lock_inspection()
    reports = [f'ESTADO DB.LCK: {status}\n{reason}', '\nESPAÇO EM DISCO:\n'+space_snapshot()]
    for title, cmd in [
        ('PACOTES KERNEL/NVIDIA', ['pacman','-Q','linux','linux-headers','nvidia','nvidia-open','nvidia-dkms','nvidia-open-dkms']),
        ('DKMS', ['dkms', 'status']),
        ('PROCESSOS', ['ps','-eo','pid,ppid,stat,etime,pcpu,comm']),
        ('ERROS DO KERNEL, BOOT ATUAL', ['journalctl', '-k', '-b', '-p', 'warning', '--no-pager', '-n', '35']),
        ('ERROS DO KERNEL, BOOT ANTERIOR', ['journalctl', '-k', '-b', '-1', '-p', 'warning', '--no-pager', '-n', '35']),
    ]:
        reports.append('\n'+title+':\n'+_diag_command(cmd))
    try:
        log = Path('/var/log/pacman.log')
        if log.is_file():
            from collections import deque
            with log.open(encoding='utf-8', errors='replace') as f:
                reports.append('\nÚLTIMAS 80 LINHAS DO PACMAN.LOG:\n'+''.join(deque(f,maxlen=80)))
    except OSError as exc:
        reports.append('\nPACMAN.LOG: '+str(exc))
    return '\n'.join(reports)


def missing_dependencies() -> list[str]:
    return [cmd for cmd in ('checkupdates', 'yay', 'pacman', 'sudo', 'script') if not shutil.which(cmd)]


def space_snapshot() -> str:
    lines = []
    for path in ('/', str(Path.home())):
        try:
            d = shutil.disk_usage(path)
            lines.append(f'{path}: {d.free / 1024**3:.1f} GiB livres / {d.total / 1024**3:.1f} GiB totais')
        except OSError as exc:
            lines.append(f'{path}: {exc}')
    return '\n'.join(lines)
