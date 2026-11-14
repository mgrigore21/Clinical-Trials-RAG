# 01 - Setup: tools, settings and automatic checks

## What we're doing

Getting your Mac ready to build the project:

1. Installing the tools we need: **uv** (Python), **Docker Desktop** (database) and **Ollama**
   (local AI models).
2. Creating the Python project and installing its libraries.
3. Adding **automatic checks** that run on every `git commit`, so mistakes are caught early.
4. Putting every setting in one place (`app/config.py` and a `.env` file).

## Why

- **Same setup for everyone.** If the Python version and library versions are pinned, "it works on my
  machine" stops being a problem.
- **Catch mistakes before they spread.** Formatting and obvious errors are fixed or flagged
  automatically, so you never have to think about them during review.
- **Settings outside the code.** The code that runs on your laptop must be *the same code* that
  runs on AWS. Only the settings change, so they live in environment variables, not in the code.

## How it works

### The tools

| Tool | What it is | Used for |
|---|---|---|
| **git** | Version control: a history of every change | Branches, one per step |
| **uv** | A fast Python manager | Installs Python 3.12 and our libraries into an isolated **virtual environment** (`.venv/`) |
| **Docker Desktop** | Runs programs in lightweight isolated boxes called **containers** | Postgres with pgvector, and later the app itself |
| **Ollama** | Runs AI models on your own computer | Writing answers (`llama3.1:8b`) and embeddings (`mxbai-embed-large`) |

Why is Ollama installed directly on your Mac and not in Docker? Docker on a Mac can't use the
graphics chip (GPU), so a model inside a container is very slow. Ollama on the Mac uses it fully.

### The Python project files

- `pyproject.toml`: the project's identity card: name, Python version, libraries, and the settings
  for our tools (ruff, pytest).
- `uv.lock`: the exact version of every library, including the libraries our libraries need. It
  is generated; don't edit it by hand. Commit it.
- `.python-version`: tells uv which Python to use (3.12).

### Automatic checks (pre-commit)

A **git hook** is a script git runs at certain moments. Ours runs just before each commit and
checks the files you are about to commit. It is configured in `.pre-commit-config.yaml`:

- **ruff** is a **linter** (spots likely bugs and messy code) and a **formatter** (lays code out in
  one consistent style). It fixes most things by itself.
- A few simple checks: no trailing spaces, files end with a newline, YAML/TOML files are valid,
  no file bigger than 500 KB (this keeps downloaded trial data out of git), and no accidentally
  committed private keys.

If a check **fixes** a file, the commit is stopped. That is intended: look at what changed, add the
files again with `git add`, and commit again.

### Settings

`app/config.py` reads settings from **environment variables**, which are named values in the
program's surroundings. During development they come from a file called `.env`. `make setup`
copies `.env.example` to `.env` for you. `.env` is ignored by git because real projects keep
secrets there. Ours has none yet, but the habit is the right one.

| Setting | Default | Meaning |
|---|---|---|
| `PROVIDER` | `ollama` | Which AI backend: `ollama` (local) or `bedrock` (AWS) |
| `DATABASE_URL` | `postgresql://rag:rag@localhost:5432/trials` | Where the database is |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens |
| `LLM_MODEL` | `llama3.1:8b` | The model that writes answers |
| `EMBEDDING_MODEL` | `mxbai-embed-large` | The model that turns text into numbers |
| `EMBEDDING_DIM` | `1024` | How many numbers per embedding. Same in every environment |
| `TOP_K` | `8` | How many trials retrieval hands to the model |
| `AWS_REGION` | `eu-central-1` | Only used with `PROVIDER=bedrock` |

If a setting is invalid, the app refuses to start with a clear message instead of failing later in
a confusing way (you will try this below).

### The Makefile

`make <name>` is a shortcut for a longer command so you don't have to remember it.
`make help` lists them.

## Try it yourself

All commands run from the repository folder (`Clinical-Trials-RAG`).

### 1. Install the tools

**git and uv** (you probably have them already):

```bash
git --version
uv --version
```

Expected: a version line each, e.g. `git version 2.54.0 ...` and `uv 0.13.7 ...`. If `uv` is missing,
install it from <https://docs.astral.sh/uv/getting-started/installation/>.

**Docker Desktop**: download the installer for your chip (Apple silicon or Intel) from
<https://www.docker.com/products/docker-desktop/>, open the `.dmg`, drag Docker to Applications,
and start it once (it asks for your password and to accept the terms). Wait until the whale icon in
the menu bar stops animating. Then:

```bash
docker --version
docker run --rm hello-world
```

Expected: a version line, then a message that includes `Hello from Docker!`.

**Ollama**: download from <https://ollama.com/download>, open it, and move it to Applications. Start it
once. It then runs in the background (llama icon in the menu bar). Then:

```bash
ollama --version
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
ollama list
```

Expected: the two `pull` commands download about 4.9 GB and 0.7 GB (a few minutes, depending on your
connection) and end with `success`. `ollama list` then shows both models.

Check that the Ollama server answers:

```bash
curl http://localhost:11434/api/tags
```

Expected: a line of JSON that mentions `llama3.1:8b` and `mxbai-embed-large`.

**GitHub CLI (`gh`)**: optional but handy. It lets you push branches and open pull requests from the
terminal. The simplest install is Homebrew (`brew install gh`). Without Homebrew, download the
**macOS `.pkg`** from <https://github.com/cli/cli/releases/latest> (pick the file ending in `arm64` on
Apple silicon) and open it. Then log in once (answer: GitHub.com, HTTPS, Yes, "Login with a web browser"):

```bash
gh auth login
gh auth setup-git
gh auth status
```

Expected: `gh auth status` ends with `Logged in to github.com account <your name>`.

### 2. Set up the Python project

```bash
make setup
```

Expected: uv checks its packages, then `pre-commit installed at .git/hooks/pre-commit`, and, the
first time, `Created .env from .env.example`.

```bash
uv run python --version
```

Expected: `Python 3.12.x`.

```bash
make help
```

Expected: a list with `help`, `setup`, `lint`, `format` and `test`.

### 3. Run the tests and the checks

```bash
make test
```

Expected:

```
uv run pytest
...                                                                      [100%]
3 passed in 0.03s
```

```bash
make lint
```

Expected: every line ends with `Passed` (or `Skipped` for file types the repo doesn't contain yet).

### 4. Look at the settings

```bash
uv run python -c "from app.config import get_settings; print(get_settings())"
```

Expected: one line starting with `provider='ollama' database_url='postgresql://rag:rag@localhost:5432/trials' ...`.

Now override a setting for just this one command:

```bash
PROVIDER=bedrock uv run python -c "from app.config import get_settings; print(get_settings().provider)"
```

Expected: `bedrock`. The environment variable won over the default.

And try an invalid value:

```bash
PROVIDER=openai uv run python -c "from app.config import get_settings; get_settings()"
```

Expected: an error ending in `Input should be 'ollama' or 'bedrock'`. The app refused to start with a
bad setting, which is what we want.

## Common problems

- **`Cannot connect to the Docker daemon`**: Docker Desktop is installed but not running. Open it
  from Applications and wait for the whale icon to settle.
- **`docker: command not found`, or `docker-credential-desktop: executable file not found`, even
  though Docker Desktop is running**: Docker Desktop didn't create its command-line links in
  `/usr/local/bin` (this happens when it was set up without the administrator-password option). Two
  fixes. Either open Docker Desktop, go to *Settings > Advanced*, choose **System** under
  "Installation of system components", and approve the password prompt. Or put Docker's own folder
  on your PATH by adding `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"` to
  `~/.zshrc` and opening a new terminal. Check with `which docker`.
- **`ollama pull` says `could not connect to ollama app`**: the Ollama app isn't running. Open it
  from Applications.
- **Pulling the models is very slow**: they are large downloads. It is safe to stop with Ctrl+C and
  run the same command again; Ollama resumes.
- **`make lint` fails and says `files were modified by this hook`**: a check fixed a file for you.
  Run `make lint` again; it should now pass. When committing, `git add` the changed files first.
- **`make lint` says `no files to check` for ruff / YAML / TOML**: pre-commit only looks at files
  git already knows about. Files that are brand new and not yet staged (`git add`) are skipped.
- **`gh: accepts 0 arg(s), received 4`** (or similar): you pasted a command together with its `#`
  comment. In macOS's default shell (zsh), text after `#` is **not** ignored when you type a command
  interactively, so `gh` receives the comment's words as arguments. Run the command without the comment.
- **The `gh` download only shows folders and files, nothing installs**: you got the `.zip`, not the
  `.pkg`. The program is the file `bin/gh` inside. Either download the `.pkg`, or copy that file into a
  folder on your PATH, e.g. `mkdir -p ~/.local/bin && cp bin/gh ~/.local/bin/`.
- **`uv: command not found`**: install uv (link above), then open a new terminal.
- **`.env` changes seem ignored**: a variable set in your shell wins over the `.env` file. Check
  with `env | grep -E 'PROVIDER|TOP_K'`.
