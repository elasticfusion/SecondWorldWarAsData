#!/bin/bash
# Setup OpenSERP Integration

set -e

echo "🔧 Setting up OpenSERP integration..."

# Detect architecture
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64) GOARCH=amd64 ;;
    aarch64|arm64) GOARCH=arm64 ;;
    *) echo "❌ Unsupported architecture: $ARCH"; exit 1 ;;
esac
echo "   Detected: ${OS}/${GOARCH}"

# 1. Clone and build OpenSERP
echo ""
echo "1️⃣ Cloning OpenSERP..."
if [ ! -d "openserp" ]; then
    git clone https://github.com/karust/openserp.git
fi
cd openserp
echo "Building OpenSERP for ${OS}/${GOARCH}..."
GOOS=$OS GOARCH=$GOARCH go build -o openserp .
cd ..
echo "✅ OpenSERP built ($(file openserp/openserp | sed 's/.*: //'))"

# 2. Start OpenSERP server in background
echo ""
echo "2️⃣ Starting OpenSERP server..."
cd openserp
./openserp serve -p 7001 &
OPENSERP_PID=$!
cd ..
sleep 2

# 3. Test OpenSERP
echo ""
echo "3️⃣ Testing OpenSERP..."
if curl -s "http://localhost:7001/mega/search?text=test&limit=1" > /dev/null; then
    echo "✅ OpenSERP is running (PID: $OPENSERP_PID)"
    echo $OPENSERP_PID > .openserp.pid
else
    echo "❌ OpenSERP failed to start"
    exit 1
fi

# 4. Build search tools
echo ""
echo "4️⃣ Building search tools for ${OS}/${GOARCH}..."
GOOS=$OS GOARCH=$GOARCH go build -o search_maps search_maps.go
GOOS=$OS GOARCH=$GOARCH go build -o search_media search_media.go
echo "✅ Built search_maps and search_media"

echo ""
echo "✅ Setup complete!"
echo ""
echo "Usage:"
echo "  ./search_maps -place \"Normandy\" -date \"1944-06-06\""
echo ""
echo "Stop OpenSERP:"
echo "  kill \$(cat .openserp.pid)"

