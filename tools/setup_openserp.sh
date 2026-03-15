#!/bin/bash
# Setup OpenSERP Integration

echo "🔧 Setting up OpenSERP integration..."

# 1. Clone and build OpenSERP
echo ""
echo "1️⃣ Cloning OpenSERP..."
if [ ! -d "openserp" ]; then
    git clone https://github.com/karust/openserp.git
    cd openserp
    echo "Building OpenSERP..."
    go build -o openserp .
    cd ..
    echo "✅ OpenSERP built"
else
    echo "✅ OpenSERP already exists"
fi

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
curl -s "http://localhost:7001/mega/search?text=test&limit=1" > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ OpenSERP is running (PID: $OPENSERP_PID)"
    echo $OPENSERP_PID > .openserp.pid
else
    echo "❌ OpenSERP failed to start"
    exit 1
fi

# 4. Build search tool
echo ""
echo "4️⃣ Building search tool..."
go build -o search_maps search_maps.go
echo "✅ Built search_maps executable"

echo ""
echo "✅ Setup complete!"
echo ""
echo "Usage:"
echo "  ./search_maps -place \"Normandy\" -date \"1944-06-06\""
echo ""
echo "Stop OpenSERP:"
echo "  kill \$(cat .openserp.pid)"

