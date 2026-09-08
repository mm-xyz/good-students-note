#!/bin/bash
# ╔════════════════════════════════════════════╗
# ║   doc-to-md 一鍵安裝器（Mac / Linux）      ║
# ║   把 PDF/EPUB/TXT 轉成 Markdown 知識庫     ║
# ╚════════════════════════════════════════════╝
#
# 使用方式：打開 Terminal → 拖入此檔案 → 按 Enter

set -e
trap 'if [ -n "${EXTRACT_DIR:-}" ]; then rm -rf "$EXTRACT_DIR"; fi' EXIT

INSTALL_DIR="$HOME/.doc-to-md"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/skill"
EXTRACT_DIR=""

if [ ! -f "$SKILL_SRC/scripts/requirements.txt" ]; then
    if [ ! -f "$SCRIPT_DIR/skill.zip" ]; then
        echo "找不到 skill.zip，請確認安裝包已完整解壓縮。"
        exit 1
    fi
    EXTRACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/doc-to-md-skill.XXXXXX")"
    echo "📦 解壓縮技能檔..."
    unzip -q "$SCRIPT_DIR/skill.zip" -d "$EXTRACT_DIR"
    SKILL_SRC="$EXTRACT_DIR/skill"
fi

if [ ! -f "$SKILL_SRC/scripts/requirements.txt" ]; then
    echo "找不到 $SKILL_SRC/scripts/requirements.txt"
    echo "請確認安裝包內容完整，或重新下載安裝包。"
    exit 1
fi

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   doc-to-md 安裝程式 v1.4.6                ║"
echo "╚════════════════════════════════════════════╝"
echo ""

# ── Step 1: 找到可用的 Python 3.8+ ──────────────────────────────────────────

echo "🔍 Step 1/3：檢查 Python 版本..."
PY=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] 2>/dev/null && [ "$minor" -ge 8 ] 2>/dev/null; then
            PY="$cmd"
            echo "   ✅ 找到 $cmd (Python $ver)"
            break
        else
            echo "   ⏭️  $cmd 版本 $ver 太舊，跳過"
        fi
    fi
done

if [ -z "$PY" ]; then
    echo ""
    echo "   ❌ 找不到 Python 3.8 以上版本"
    echo ""
    echo "   請先安裝 Python："
    echo "   👉 前往 https://www.python.org/downloads/"
    echo "   👉 下載安裝 Python 3.12（點擊 Download 大按鈕）"
    echo "   👉 安裝完成後，關閉 Terminal 重新開啟"
    echo "   👉 再次拖入此檔案執行"
    echo ""
    open "https://www.python.org/downloads/" 2>/dev/null || true
    exit 1
fi

# ── Step 2: 建立安裝目錄和 venv ─────────────────────────────────────────────

echo ""
echo "📦 Step 2/3：建立虛擬環境..."

mkdir -p "$INSTALL_DIR"

if [ -d "$INSTALL_DIR/venv" ]; then
    echo "   ⏭️  虛擬環境已存在，跳過建立"
else
    "$PY" -m venv "$INSTALL_DIR/venv"
    echo "   ✅ 虛擬環境建立在 $INSTALL_DIR/venv"
fi

# ── Step 3: 安裝 Python 依賴 + 複製腳本 ─────────────────────────────────────

echo ""
echo "📥 Step 3/3：安裝 Python 套件（可能需要 1-2 分鐘）..."

"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet 2>/dev/null
"$INSTALL_DIR/venv/bin/pip" install -r "$SKILL_SRC/scripts/requirements.txt" --quiet

if [ $? -eq 0 ]; then
    echo "   ✅ 所有套件安裝完成"
else
    echo "   ❌ 安裝失敗，請截圖錯誤訊息回報老師"
    exit 1
fi

# 複製轉換腳本到安裝目錄
cp "$SKILL_SRC/scripts/doc_to_md.py" "$INSTALL_DIR/"
cp "$SKILL_SRC/scripts/requirements.txt" "$INSTALL_DIR/"

# 建立全域啟動器
cat > "$INSTALL_DIR/doc-to-md" << 'LAUNCHER'
#!/bin/bash
DIR="$HOME/.doc-to-md"
"$DIR/venv/bin/python3" "$DIR/doc_to_md.py" "$@"
LAUNCHER
chmod +x "$INSTALL_DIR/doc-to-md"

# 加入 PATH
SHELL_RC=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bash_profile" ]; then
    SHELL_RC="$HOME/.bash_profile"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ]; then
    if ! grep -q "doc-to-md" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo '# doc-to-md converter' >> "$SHELL_RC"
        echo 'export PATH="$HOME/.doc-to-md:$PATH"' >> "$SHELL_RC"
    fi
fi

# ── 驗證 ────────────────────────────────────────────────────────────────────

echo ""
echo "🧪 驗證安裝..."
"$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/doc_to_md.py" --help >/dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "   ✅ 驗證通過！"
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                      🎉 安裝完成！                          ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║                                                            ║"
    echo "║  接下來請在 Claude Desktop 加入技能：                        ║"
    echo "║                                                            ║"
    echo "║  1. 打開 Claude Desktop                                     ║"
    echo "║  2. 點左上方 Customize → Skills → + 號                      ║"
    echo "║  3. 選 Create Skill → Upload a skill                       ║"
    echo "║  4. 上傳安裝包裡的 skill.zip                                ║"
    echo "║  5. 確認 doc-to-md 出現在 Skills 列表中                     ║"
    echo "║                                                            ║"
    echo "║  完成後，對 Claude 說：                                      ║"
    echo "║  「幫我把這個 PDF 轉成 Markdown：/路徑/檔名.pdf」            ║"
    echo "║                                                            ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  🔧 也可以手動在 Terminal 使用："
    echo "     $INSTALL_DIR/doc-to-md --auto ~/Desktop/mybook.pdf -o ~/Desktop/"
    echo ""
    echo "  ℹ️  重新開啟 Terminal 後也可以直接輸入："
    echo "     doc-to-md --auto ~/Desktop/mybook.pdf -o ~/Desktop/"
    echo ""
else
    echo "   ❌ 驗證失敗，請截圖回報老師"
    exit 1
fi
