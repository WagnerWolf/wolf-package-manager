# Wolf Package Manager 0.3.1

- Pesquisa no filtro Todos em **paralelo** para Repositório, AUR e Comandos.
- Cache LRU em memória (máx. 90 entradas, TTL 5 minutos) para buscas repetidas;
  sem persistência, sem armazenar erros e sem escrever índices no disco.
- Indicação de progresso por fonte durante pesquisa.
- Cache invalidado quando termina qualquer etapa de instalação/atualização,
  incluindo falhas após alterações parciais na transação.
- Se AUR estiver indisponível, resultados dos repositórios e comandos continuam
  disponíveis. As três fontes permanecem com filtros separados.
- Sem mudanças no fluxo de pacman/yay, permissões, db.lck, tray ou autenticação.

Validação: testes automatizados de backend e consultas em paralelo. Qt e pesquisas
reais dependem de validação no Wolf Linux.
