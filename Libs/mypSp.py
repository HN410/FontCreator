
from Libs.myFontData import FontGeneratorDataset
import torch
import torch.nn as nn
import sys

from torchvision import transforms
sys.path.append('../')
from EfficientNet.model import *
from StyleGAN.network import *



class MyPSP(nn.Module):
    # 複数画像からフォントを構成するモデル
    def __init__(self):
        # chara_encoder ... どの文字かをエンコード
        # style_encoder ... 複数のフォントの組からスタイル情報をエンコード
        # style_gen ... エンコーダから得られた情報をもとにフォントを構成
        super().__init__()
        self.z_dim = 256 # エンコーダから渡される特徴量の個数
        blocks_args, global_params = get_model_params('efficientnet-b0', {})
        self.chara_encoder = EfficientNetEncoder(blocks_args, global_params, isForCharacter=True)
        self.style_encoder = EfficientNetEncoder(blocks_args, global_params)
        load_pretrained_weights(self.chara_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(True), advprop=False)
        load_pretrained_weights(self.style_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(True), advprop=False)
        self.chara_encoder._change_in_channels(1)
        self.style_encoder._change_in_channels(1)
        gen_settings = get_setting_json()
        self.style_gen = Generator(gen_settings["network"])
    
    def set_level(self, level):
        self.style_gen.set_level(level)
    
    def forward(self, chara_images,  style_pairs, alpha):
        # chara_image ... 変換したい文字のMSゴシック体の画像
        #   [B, 1, 256, 256]
        # style_pairs ... MSゴシック体の文字と、その文字に対応する変換先のフォントの文字の画像のペアのテンソル
        #   [B, pair_n, 2, 1, 256, 256]
        # alpha ... どれだけ変化させるかの係数？バッチで共通なため、サイズは[1, 1]
        pair_n = style_pairs.size()[1]

        # 文字をエンコード [B, 256*6, 1, 1]
        chara_images = self.chara_encoder(chara_images)
        
        # ペアの差分をとる [B, pair_n, 1, 256, 256]
        style_pairs = style_pairs[:, :, 1] -  style_pairs[:, :, 0]
        # 文字ごとにencoderにかけ、その特徴量を総和する [B, 256*2, 1, 1]
        style_pairs = [self.style_encoder(style_pairs[:, i]) for i in range(pair_n)]

        style_pairs = torch.stack(style_pairs).sum(0)


        res =  self.style_gen(chara_images, style_pairs, alpha)
        return torch.sigmoid(res)

class MyPSPLoss(nn.Module):
    # MyPSP用の損失関数
    # フォントは通常の画像と異なり、訓練画像とぴったり一致するほうがよいので、二乗誤差で試す
    # onSharpはImageSharpLossにかける係数

    MSE_N = 3
    SCALE = 4

    def __init__(self, onSharp = 0):
        super().__init__()
        self.MSEs = nn.ModuleList([nn.MSELoss() for i in range(self.MSE_N)])
        if(0 < onSharp):
            self.onSharp = onSharp
            self.sharpLoss = ImageSharpLoss()
        else:
            self.sharpLoss = None
    def forward(self, outputs, targets):
        # outputs, targetsともに[B, 1, W, H]

        # onSharp == Trueで各ピクセルが0か1に近いほど小さくなるような損失も追加
        sharpScore = 0
        if(self.sharpLoss is not None):
            sharpScore = self.sharpLoss(outputs)
            sharpScore *= self.onSharp
            

        # outputsは正規化されていないので、正規化する
        outputs = transforms.Compose([
            transforms.Normalize(FontGeneratorDataset.IMAGE_MEAN, 
                FontGeneratorDataset.IMAGE_VAR)])(outputs)

        ans = [0] * self.MSE_N
        ans[0] = self.MSEs[0](outputs, targets)
        # SCALE分の1した画像でも同様に二乗誤差をとってみる
        factor = 1
        for i in range(self.MSE_N-1):
            factor *= self.SCALE ** 2
            outputs = F.interpolate(outputs, scale_factor=1/self.SCALE, mode="bilinear")
            targets = F.interpolate(targets, scale_factor=1/self.SCALE, mode="bilinear")
            ans[i+1] = self.MSEs[i+1](outputs, targets) * factor
        ans = torch.stack(ans)
        return torch.mean(ans) + sharpScore

class ImageSharpLoss(nn.Module):
    # 各ピクセルが0, 1に近いほど損失が小さくなる
    #　基本的にはx^2と(x-1)^2を場合分けで組み合わせた形

    #  正規化する前に入力すること

    def __init__(self):
        super().__init__()
    
    def forward(self, outputs):
        smaller = torch.lt(outputs, 0.5)
        bigger = torch.ge(outputs, 0.5)
        smaller = smaller * outputs**2
        bigger = bigger * (outputs-1)**2
        return (smaller + bigger).mean()