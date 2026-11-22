# CP Coach — Linux

CP Tools numa janela de secretária, com controlo local do uso de IA. Os dois ficheiros originais estão intactos; o painel é acrescentado apenas ao servir a página.

## Executar

Extraia o arquivo para uma pasta permanente. Precisa de Python 3. Execute:

```sh
python3 cp-coach.py
```

Se não existir uma janela pywebview funcional, abre um navegador. `python3 cp-coach.py --browser` força essa opção; mantenha o terminal aberto e termine com Ctrl+C. `--serve-only` serve sem abrir uma janela; o endereço e a porta livre aparecem no terminal.

Opcional, recomendado para bloqueio de rede e janela nativa:

```sh
./install.sh
```

Execute como utilizador normal com acesso a sudo. Em Ubuntu/Debian tenta instalar GTK/WebKit (4.1, ou 4.0 onde necessário) e cria `.venv` com pywebview 6.2+ e acesso aos pacotes do sistema. A aplicação usa automaticamente este ambiente nas aberturas seguintes; o atalho também o usa. Noutras distribuições instale Python, pywebview recente e um backend GTK/WebKit ou Qt pelos meios da distribuição. O instalador cria um atalho no menu com o ícone `cp-coach.svg` (copiado para `~/.local/share/icons/hicolor/scalable/apps/`), instala um helper root-owned em `/usr/local/lib/cp-coach/` e uma regra sudo limitada às suas três operações. Não execute a aplicação como root. Se a janela falhar, use `--browser`.

A janela usa armazenamento persistente (localStorage e IndexedDB) em `~/.local/share/cp-coach/webview`, com o modo privado desativado, tanto em GTK como em Qt. Isto é necessário para o funcionamento dos separadores e da sincronização com `/data`; a pasta pode conter dados privados e é mantida na desinstalação. O ficheiro `cp-tools-data.json` continua a ser a cópia partilhável entre computadores.

## Modos e dados

- **Prática com IA**: sem bloqueio; levanta um bloqueio anterior quando não há sessão ativa.
- **Prática sem IA**: bloqueio ativo, pode terminar a qualquer momento.
- **Contest**: escolha a duração (minutos). Termina automaticamente; terminar antes exige escrever `TERMINAR CONTEST` e regista uma violação. Fechar a janela não termina a sessão nem remove o bloco. Volte a abrir para retomar; se o prazo já passou, remove-o no arranque. Com a aplicação fechada não há monitorização nem remoção automática no instante do prazo.

O painel apresenta o estado real da rede, sessões e violações. Sem autorização administrativa, o bloqueio fica indisponível com indicação do motivo, mas as verificações continuam. A cada cerca de 30 segundos verifica o bloco e procura visitas em históricos locais Chromium/Chrome/Brave/Edge/Vivaldi/Opera e Firefox (incluindo perfis e localizações snap/flatpak). Há nova leitura do histórico ao terminar. A ausência do bloco gera um registo e uma tentativa de reposição.

**Bloqueio de agentes de terminal:** em Prática sem IA e Contest, procura processos do seu utilizador a cada cerca de 5 segundos: Claude Code, Devin CLI, Codex, Gemini CLI, Aider, GitHub Copilot CLI, OpenCode, Cursor Agent, Goose, Crush, Amp, Qwen Code, llm e Ollama, além das aplicações gráficas conhecidas. Identifica o executável ou o caminho do script Node/Python/shell, não palavras em argumentos arbitrários. Envia SIGTERM e, se persistirem, SIGKILL após cerca de 3 segundos; regista processo, excerto da linha de comando e resultado. Pode perder trabalho não guardado nestes processos. Não termina o próprio CP Coach, processos de outros utilizadores ou o servidor web do IDE VS Code chamado `devin serve-web` (não é o agente CLI). Em Prática com IA não termina processos. As APIs conhecidas destes serviços também estão na lista de hosts; não bloqueia GitHub ou Google APIs na totalidade.

Guarde o seu `cp-tools-data.json` existente nesta pasta: o formato e `/data` GET/PUT continuam iguais. Sessões, URLs visitados e violações ficam em `cp-coach-blocker.json`, também local; trate-o como informação privada. O estado e o histórico não são invioláveis. Não há envio de relatórios para terceiros.

## Limitações importantes

O helper altera apenas o bloco entre marcadores `cp-coach` em `/etc/hosts`, apontando os domínios conhecidos e variantes `www` para `0.0.0.0` e `::`: ChatGPT/OpenAI, Claude/Anthropic, Devin/Cognition, Gemini, Copilot, Perplexity, DeepSeek, Poe, Grok, Meta AI, Mistral, Hugging Face, Phind, You, Character AI, Codeium/Windsurf, Cursor, Tabnine, Blackbox, Groq, OpenRouter, Together, Ollama, Amp, OpenCode, Moonshot e DashScope. A lista completa está no helper. Não há wildcards: só bloqueia os hosts explicitamente listados. **Reinicie o navegador ao ativar o bloqueio**, por causa das caches DNS e ligações já abertas.

Não é um sistema anti-batota infalível: sudo permite editar hosts e remover o helper; VPN/proxy, IP direto, DNS próprio, outros dispositivos e serviços desconhecidos podem contornar o controlo. Modelos locais e ferramentas fora da lista não estão cobertos; renomear agentes ou usar outros invólucros pode contornar a deteção. O histórico privado/apagado/não gravado não é detetável; perfis inacessíveis podem falhar. O histórico é copiado com WAL para leitura, mas pode haver alterações concorrentes. A deteção por nomes conhecidos pode falhar ou gerar falsos positivos; o bloqueio só atua enquanto a aplicação está em execução e pode haver alguns segundos de uso antes da deteção. **Partilha/captura de ecrã não implementada.** Não substitui as regras do contest.

## Desinstalar e recuperar

Feche primeiro o CP Coach e execute `./uninstall.sh`. Remove o bloco, o helper, a regra sudo e o atalho; mantém a pasta, os dados e dependências partilhadas. Pode apagar a pasta e `.venv` depois de guardar os seus dados.

Para desbloquear manualmente antes da desinstalação: `sudo /usr/local/lib/cp-coach/cp-coach-hosts-helper remove`. Numa sessão ativa, feche primeiro a aplicação ou esta tentará repor o bloco. Sem helper instalado: `sudo ./cp-coach-hosts-helper remove` na pasta extraída. Como último recurso, remova apenas as linhas entre `# BEGIN cp-coach` e `# END cp-coach` de `/etc/hosts`, incluindo os marcadores.
