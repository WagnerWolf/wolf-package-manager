# Wolf Package Manager 0.3.2

- Verificação de atualizações mais confiável e sem alteração destrutiva do banco do pacman.
- Detecta divergência entre `checkupdates` e `pacman -Qu` quando a primeira consulta retorna zero.
- Descarta resultados de verificações que se sobreponham a transação externa registrada no pacman.log ou a uma trava presente.
- Distingue consulta incompleta de sistema atualizado; retenta após 60 s quando uma transação foi detectada.
- Busca e instalação continuam como na versão 0.3.1.
