# Wolf Package Manager 0.3.1

Protótipo independente do **Wolf Linux 2.0.0**, em PySide6, com pacman + yay, verificação periódica e bandeja KDE.

## Instalação (usuário comum)

```bash
sudo pacman -S --needed pyside6 pacman-contrib util-linux
# Necessários também: pacman, sudo, yay
./iniciar.sh
./install-local.sh
```

Encerre a versão anterior pelo menu da bandeja antes de substituir os arquivos; depois reinicie o gerenciador. O instalador grava os lançadores em `~/.local/bin`, no menu de aplicações e em `~/.config/autostart`. Para não abrir janela no login, o autostart usa **`wolf-package-manager --tray`**. A abertura pelo menu usa o comando sem parâmetro. Se o Plasma não oferecer bandeja, a janela aparece para não sumir silenciosamente.

## Pesquisa mais rápida (0.3.1)

No filtro **Todos**, as fontes Repositório, AUR e Comandos são consultadas em
paralelo, em vez de uma após a outra. O aplicativo informa o avanço por fonte.
Resultados de pesquisas repetidas são mantidos **somente em memória** por até
cinco minutos, evitando repetir consultas à rede e ao índice de arquivos. A
memória de pesquisas é descartada após instalação ou atualização bem-sucedida.
A busca AUR ainda depende da rede e pode continuar lenta quando o servidor
estiver indisponível. Use o filtro Repositório para consultas exclusivamente locais.

## Pesquisa e instalações em lote

- Filtro **Todos / Repositório / AUR / Comandos**. Em **Todos**, pesquisa também arquivos de comandos disponíveis nos índices de repositórios.
- Para pesquisar quem fornece `nc`, é preciso ter os bancos `repo.files` sincronizados. Execute **uma vez** `sudo pacman -Fy` (ou atualize quando o índice estiver desatualizado). A busca consulta `pacman -F --machinereadable` sem elevar privilégios. Os arquivos AUR não estão nesse índice.
- Marque as caixas da primeira coluna para escolher vários pacotes. O botão **Instalar pacotes marcados** executa um único `sudo -A pacman -Syu` para os repositórios e, caso haja seleção AUR, uma única chamada `yay -S --aur --needed` como usuário comum, **somente após sucesso do pacman**.
- Instalações e atualizações exigem confirmação gráfica, e o `sudo -A` pede senha em diálogo. O terminal PTY continua com entrada manual para casos especiais.

## Segurança e diagnóstico

- A presença de `/var/lib/pacman/db.lck` bloqueia novas operações iniciadas pelo gerenciador, mesmo quando aparenta ser uma trava antiga.
- O botão **Analisar db.lck** mostra processos relevantes, idade e utilização visível pelo `fuser`; **não remove** a trava automaticamente. Se estiver inconclusivo, não remova a trava; examine processos de pacman, hooks e DKMS antes de qualquer intervenção. Não reinicie durante transações.
- **Diagnóstico** coleta leitura de `/var/log/pacman.log`, `dkms status`, pacotes kernel/NVIDIA, mensagens de kernel do boot atual/anterior, processos e espaço. Logs de operações ficam em `~/.local/state/wolf-package-manager/logs/` com `0600`; podem conter nomes de arquivos e caminhos. Revise antes de compartilhar.
- A ausência de saída por 90s gera aviso, mas não encerra transações automaticamente. `--noconfirm` não impede todas as perguntas ou falhas; AUR pode executar scripts de terceiros, por isso a opção automática pode ser desativada.
- Este aplicativo **não é um substituto para as ferramentas de recuperação** do Arch. Um bloqueio durante DKMS/mkinitcpio exige checagem dos logs antes de atualizar novamente ou reiniciar.

## Testes

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q wolfpkg
bash -n iniciar.sh install-local.sh uninstall-local.sh wolfpkg/askpass.sh
```

A aplicação Qt e as transações reais ainda precisam ser validadas no KDE. O DevKit oficial 2.0.0 não foi alterado.
