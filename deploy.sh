#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  SwingIQ — Deploy Script
#  Usage:
#    ./deploy.sh railway    → deploy to Railway
#    ./deploy.sh render     → deploy to Render (opens browser)
#    ./deploy.sh docker     → build & run Docker locally
#    ./deploy.sh check      → verify all files are present
# ─────────────────────────────────────────────────────────────────────────────
set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}ℹ  $1${NC}"; }
success() { echo -e "${GREEN}✓  $1${NC}"; }
warn()    { echo -e "${YELLOW}⚠  $1${NC}"; }
error()   { echo -e "${RED}✗  $1${NC}"; exit 1; }
header()  { echo -e "\n${BOLD}── $1 ──────────────────────────────────${NC}"; }

# ── File check ────────────────────────────────────────────────────────────────
check_files() {
  header "Checking required files"
  REQUIRED=("main.py" "database.py" "auth.py" "video_processor.py" "requirements.txt" "Dockerfile")
  ALL_OK=true
  for f in "${REQUIRED[@]}"; do
    if [ -f "$f" ]; then success "$f"; else warn "MISSING: $f"; ALL_OK=false; fi
  done
  if [ "$ALL_OK" = false ]; then error "Some files are missing. Please ensure all files are in the current directory."; fi
  success "All required files present"
}

# ── .env check ────────────────────────────────────────────────────────────────
check_env() {
  header "Checking environment"
  if [ -f ".env" ]; then
    if grep -q "ANTHROPIC_API_KEY=sk-ant-" .env; then
      success "ANTHROPIC_API_KEY found in .env"
    else
      warn "ANTHROPIC_API_KEY not set or looks invalid in .env"
      warn "Edit .env and set: ANTHROPIC_API_KEY=sk-ant-..."
    fi
  else
    warn ".env file not found — copy .env.example and fill in your key"
    warn "  cp .env.example .env"
  fi
}

# ── Railway ───────────────────────────────────────────────────────────────────
deploy_railway() {
  header "Deploying to Railway"

  if ! command -v railway &>/dev/null; then
    info "Railway CLI not found. Installing..."
    if command -v npm &>/dev/null; then
      npm install -g @railway/cli
    elif command -v brew &>/dev/null; then
      brew install railway
    else
      error "Please install Railway CLI manually: https://docs.railway.app/develop/cli"
    fi
  fi
  success "Railway CLI available"

  info "Logging in to Railway..."
  railway login

  # Check if project exists
  if railway status &>/dev/null 2>&1; then
    info "Existing Railway project detected — deploying update"
  else
    info "Creating new Railway project..."
    railway init --name swingiq-api
  fi

  header "Setting environment variables"
  # Load from .env if exists
  if [ -f ".env" ]; then
    while IFS='=' read -r key value; do
      [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
      value="${value%\"}"
      value="${value#\"}"
      railway variables set "$key=$value" --yes 2>/dev/null && success "Set $key" || warn "Could not set $key"
    done < .env
  else
    warn "No .env file — set variables manually in Railway dashboard"
    warn "Required: ANTHROPIC_API_KEY, JWT_SECRET"
  fi

  header "Deploying..."
  railway up --detach
  success "Deploy triggered!"

  echo ""
  info "Getting deployment URL..."
  sleep 3
  railway open || true

  echo ""
  echo -e "${BOLD}Next step:${NC}"
  echo -e "  Update swingiq-v5.html with your Railway URL:"
  echo -e "  ${YELLOW}window.SWINGIQ_BACKEND = 'https://your-project.railway.app';${NC}"
  echo -e "  (Add this line before </body> in swingiq-v5.html)"
}

# ── Render ────────────────────────────────────────────────────────────────────
deploy_render() {
  header "Deploying to Render"

  echo ""
  info "Render deployment steps:"
  echo ""
  echo -e "  ${BOLD}1.${NC} Go to https://render.com and sign in"
  echo -e "  ${BOLD}2.${NC} Click 'New +' → 'Web Service'"
  echo -e "  ${BOLD}3.${NC} Connect your GitHub repo (push these files first)"
  echo -e "  ${BOLD}4.${NC} Render auto-detects Dockerfile — click 'Deploy'"
  echo -e "  ${BOLD}5.${NC} In 'Environment' tab, add:"
  echo -e "       ${YELLOW}ANTHROPIC_API_KEY${NC} = sk-ant-..."
  echo -e "       ${YELLOW}JWT_SECRET${NC}         = (any long random string)"
  echo ""
  info "Or use render.yaml (Blueprint):"
  echo -e "  ${BOLD}1.${NC} Push repo to GitHub"
  echo -e "  ${BOLD}2.${NC} Go to https://render.com/deploy"
  echo -e "  ${BOLD}3.${NC} Render picks up render.yaml automatically"
  echo ""

  if command -v open &>/dev/null; then
    info "Opening Render in browser..."
    open "https://render.com/deploy"
  elif command -v xdg-open &>/dev/null; then
    xdg-open "https://render.com/deploy"
  fi

  echo ""
  echo -e "${BOLD}After deployment, update frontend:${NC}"
  echo -e "  ${YELLOW}window.SWINGIQ_BACKEND = 'https://swingiq-api.onrender.com';${NC}"
}

# ── Docker local ──────────────────────────────────────────────────────────────
deploy_docker() {
  header "Building & running Docker locally"

  if ! command -v docker &>/dev/null; then
    error "Docker not found. Install from https://docker.com"
  fi

  # Load API key
  API_KEY=""
  if [ -f ".env" ]; then
    API_KEY=$(grep "ANTHROPIC_API_KEY" .env | cut -d'=' -f2 | tr -d '"')
  fi
  if [ -z "$API_KEY" ]; then
    read -p "Enter your ANTHROPIC_API_KEY: " API_KEY
  fi

  info "Building Docker image..."
  docker build -t swingiq-api:latest .
  success "Image built"

  # Stop existing container
  docker stop swingiq-api 2>/dev/null || true
  docker rm   swingiq-api 2>/dev/null || true

  info "Starting container..."
  docker run -d \
    --name swingiq-api \
    -p 8000:8000 \
    -e ANTHROPIC_API_KEY="$API_KEY" \
    -e JWT_SECRET="local-dev-secret-$(date +%s)" \
    -v swingiq-data:/app/data \
    swingiq-api:latest

  success "Container running!"
  echo ""
  info "API:      http://localhost:8000"
  info "Docs:     http://localhost:8000/docs"
  info "Health:   http://localhost:8000/health"
  echo ""
  info "Logs:     docker logs -f swingiq-api"
  info "Stop:     docker stop swingiq-api"
}

# ── Git init helper ───────────────────────────────────────────────────────────
init_git() {
  header "Initialising git repository"

  if [ ! -d ".git" ]; then
    git init
    success "Git repo initialised"
  else
    info "Git repo already exists"
  fi

  # Create .gitignore
  cat > .gitignore << 'GITIGNORE'
.env
__pycache__/
*.pyc
*.pyo
*.db
swingiq.db
/models/*.task
/tmp/
.venv/
venv/
*.egg-info/
dist/
build/
GITIGNORE
  success ".gitignore created"

  git add -A
  git commit -m "SwingIQ v3.1 — initial commit" 2>/dev/null || true
  success "Files committed"

  echo ""
  warn "Now push to GitHub:"
  echo "  git remote add origin https://github.com/YOUR_USERNAME/swingiq.git"
  echo "  git push -u origin main"
}

# ── Entry point ───────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ███████╗██╗    ██╗██╗███╗   ██╗ ██████╗    ██╗ ██████╗ "
echo "  ██╔════╝██║    ██║██║████╗  ██║██╔════╝    ██║██╔═══██╗"
echo "  ███████╗██║ █╗ ██║██║██╔██╗ ██║██║  ███╗   ██║██║   ██║"
echo "  ╚════██║██║███╗██║██║██║╚██╗██║██║   ██║   ██║██║▄▄ ██║"
echo "  ███████║╚███╔███╔╝██║██║ ╚████║╚██████╔╝   ██║╚██████╔╝"
echo "  ╚══════╝ ╚══╝╚══╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝    ╚═╝ ╚══▀▀═╝ "
echo -e "${NC}${BLUE}  Deploy Script v1.0${NC}"
echo ""

case "${1:-help}" in
  railway) check_files; check_env; deploy_railway ;;
  render)  check_files; check_env; deploy_render  ;;
  docker)  check_files; check_env; deploy_docker  ;;
  git)     init_git ;;
  check)   check_files; check_env ;;
  *)
    echo "Usage: ./deploy.sh <command>"
    echo ""
    echo "Commands:"
    echo "  railway   Deploy to Railway (recommended, free tier)"
    echo "  render    Deploy to Render (with persistent disk)"
    echo "  docker    Build & run Docker locally"
    echo "  git       Initialise git repo + .gitignore"
    echo "  check     Verify all files & env vars"
    echo ""
    echo "Quickstart:"
    echo "  1. ./deploy.sh check"
    echo "  2. ./deploy.sh git"
    echo "  3. ./deploy.sh railway"
    ;;
esac
