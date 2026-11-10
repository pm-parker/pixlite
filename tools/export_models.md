# 模型导出指南（一次性准备工作）

## 1. face-parsing (BiSeNet → ONNX)
```bash
git clone https://github.com/zllrunning/face-parsing.PyTorch
cd face-parsing.PyTorch
# 下载 79999_iter.pth (官方 release)
pip install torch torchvision
python - <<'EOF'
import torch
from model import BiSeNet
net = BiSeNet(n_classes=19)
net.load_state_dict(torch.load('79999_iter.pth', map_location='cpu'))
net.eval()
dummy = torch.randn(1,3,512,512)
torch.onnx.export(net, dummy, 'face_parsing_bisenet.onnx',
                  input_names=['input'], output_names=['out'],
                  opset_version=13,
                  dynamic_axes={'input':{0:'b'},'out':{0:'b'}})
EOF
cp face_parsing_bisenet.onnx C:\Users\Administrator\pixlite\engine\perception\models\
```

## 2. MediaPipe FaceMesh
pip 包自带模型，无需导出。首次运行自动加载。

## 3. matting (MODNet, 可选 v0.2)
```bash
# https://github.com/ZHKKKe/MODNet → onnx 导出同理
```

## 4. 超清/修复 (可选 v0.4)
- Real-ESRGAN onnx: https://github.com/xinntao/Real-ESRGAN/releases
- LaMa inpaint onnx: https://github.com/advimman/lama
