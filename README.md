# Canvas Course Manager

A browser-based GUI for managing Canvas LMS courses. Built with Flask and vanilla JavaScript.

## Features

- **Browse** — Expandable module tree with items grouped by type (assignments, quizzes, pages, discussions)
- **Calendar** — Month-by-month view showing assignment availability windows and quiz due dates
- **Student Progress** — Ranked scores, grade distribution, missing/late submissions, per-assignment averages
- **File Manager** — Two-panel view: local files on the left, Canvas files on the right, with drag-and-drop upload
- **Assignment Editor** — Create, edit, publish/unpublish, and delete assignments with full date control
- **Quiz Editor** — Create quizzes, add/edit/delete questions, change correct answers, inline save
- **LaTeX → Canvas** — Side-by-side editor that converts LaTeX equations into Canvas equation image format, with live preview, AI-assisted LaTeX generation, and direct save to Canvas pages
- **AI Assistant** — Chat interface powered by Claude Code with Canvas MCP tools for course queries

## Prerequisites

- Python 3.10+
- A Canvas LMS account with a valid API token
- (Optional) [Claude Code](https://claude.ai/claude-code) for the AI Assistant tab

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/astrostubbs/CanvasFrontEnd.git
cd CanvasFrontEnd
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
CANVAS_API_TOKEN=your_canvas_api_token_here
CANVAS_API_URL=https://your-canvas-instance.instructure.com/api/v1
LOCAL_ROOT=~/path/to/your/course/files
```

**Getting a Canvas API token:**
1. Log in to your Canvas instance
2. Go to Account → Settings
3. Scroll to "Approved Integrations"
4. Click "+ New Access Token"
5. Give it a purpose (e.g., "Course Manager") and click "Generate Token"
6. Copy the token immediately — it won't be shown again

### 3. Export environment and run

```bash
set -a; source .env; set +a
python canvas_gui.py
```

Open [http://localhost:5050](http://localhost:5050) in your browser.

## MCP Configuration (for AI Assistant)

The AI Assistant tab uses Claude Code with a Canvas MCP (Model Context Protocol) server. To enable it:

1. Install Claude Code: `npm install -g @anthropic-ai/claude-code`
2. Create a `canvas.mcp.json` file in the project directory:

```json
{
    "type": "http",
    "url": "https://your-canvas-mcp-server/mcp",
    "headers": {
        "X-Canvas-API-Token": "your_canvas_api_token_here",
        "X-Canvas-API-URL": "https://your-canvas-instance.instructure.com"
    }
}
```

3. Make sure your Anthropic API credentials are set (e.g., `ANTHROPIC_API_KEY`) or that you have a Bedrock gateway configured.

The AI Assistant inherits your shell environment variables, so any Claude Code configuration (Bedrock, API keys) should be exported before starting the server.

## Project Structure

```
CanvasFrontEnd/
├── canvas_gui.py          # Flask backend — API proxy + local file serving
├── templates/
│   └── index.html         # Single-page frontend (all HTML/CSS/JS)
├── docs/
│   └── user_guide.tex     # LaTeX user guide
├── .env.example           # Template for environment variables
├── .gitignore
├── requirements.txt
├── LICENSE                # CC BY-NC 4.0
└── README.md
```

## Security Notes

- **Never commit your API token.** The `.gitignore` excludes `.env` and `canvas.mcp.json`.
- The app runs locally on `127.0.0.1:5050` — it is not designed for public deployment.
- Canvas API tokens grant full access to your account. Treat them like passwords.

## License

Copyright (c) 2025 Christopher Stubbs, Harvard University

This work is licensed under a [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/).

You are free to share and adapt this software for non-commercial purposes, with attribution. See [LICENSE](LICENSE) for details.
