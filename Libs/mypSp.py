
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
    def __init__(self, ver = 1, dropout_p = 0):
        # chara_encoder ... どの文字かをエンコード
        # style_encoder ... 複数のフォントの組からスタイル情報をエンコード
        # style_gen ... エンコーダから得られた情報をもとにフォントを構成
        super().__init__()
        self.z_dim = 256 # エンコーダから渡される特徴量の個数
        blocks_args, global_params = get_model_params('efficientnet-b0', {})
        self.chara_encoder = EfficientNetEncoder(blocks_args, global_params, isForCharacter=True, ver=ver)
        self.style_encoder = EfficientNetEncoder(blocks_args, global_params, ver = ver)
        load_pretrained_weights(self.chara_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(ver < 2), advprop=False)
        load_pretrained_weights(self.style_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(True), advprop=False)
        self.chara_encoder._change_in_channels(1)
        self.style_encoder._change_in_channels(1)
        gen_settings = get_setting_json()
        self.style_gen = Generator(gen_settings["network"], ver=ver, dropout_p=dropout_p)
        self.for_chara_training = False
        self.ver = ver
    
    def set_level(self, level):
        self.style_gen.set_level(level)
    
    def set_for_chara_training(self, b):
        # 文字のエンコードデコードのみを訓練するとき
        self.style_gen.set_for_chara_training(b)
        self.for_chara_training = b
    
    def forward(self, chara_images,  style_pairs, alpha):
        # chara_image ... 変換したい文字のMSゴシック体の画像
        #   [B, 1, 256, 256]
        # style_pairs ... MSゴシック体の文字と、その文字に対応する変換先のフォントの文字の画像のペアのテンソル
        #   [B, pair_n, 2, 1, 256, 256]
        # alpha ... どれだけ変化させるかの係数？バッチで共通なため、サイズは[1, 1]

        # 文字をエンコード [B, 256*6, 1, 1](ver1) or [B, 320, 8, 8](ver2)
        chara_images = self.chara_encoder(chara_images)

        if self.for_chara_training:
            if self.ver >= 3:
                return self.style_gen(chara_images, None, alpha)
            else:
                return torch.sigmoid(self.style_gen(chara_images, None, alpha))
        
        pair_n = style_pairs.size()[1]
        # ペアの差分をとる [B, pair_n, 1, 256, 256]
        style_pairs = style_pairs[:, :, 1] -  style_pairs[:, :, 0]
        # 文字ごとにencoderにかけ、その特徴量を総和する [B, 256*2, 1, 1]
        style_pairs = [self.style_encoder(style_pairs[:, i]) for i in range(pair_n)]

        style_pairs = torch.stack(style_pairs).mean(0)


        res =  self.style_gen(chara_images, style_pairs, alpha)

        return torch.sigmoid(res)

class MyPSPLoss(nn.Module):
    # MyPSP用の損失関数
    # フォントは通常の画像と異なり、訓練画像とぴったり一致するほうがよいので、二乗誤差で試す
    # onSharpはImageSharpLossにかける係数

    MSE_N = 3
    SCALE = 4

    def __init__(self, onSharp = 0, rareP = 0, separateN = 1, hingeLoss = 0):
        super().__init__()
        self.MSEs = nn.ModuleList([nn.MSELoss() for i in range(self.MSE_N)])
        if(0 < onSharp):
            self.onSharp = onSharp
            self.sharpLoss = ImageSharpLoss()
        else:
            self.sharpLoss = None
        if(0 < rareP):
            self.rareP = rareP
            self.rareLoss = ImageRarePixelLoss(separateN)
        else:
            self.rareP = None
        if(hingeLoss > 0):
            self.hingeLoss = ImageHingeLoss()
        else:
            self.hingeLoss = None
    def forward(self, outputs, targets):
        # outputs, targetsともに[B, 1, W, H]

        # onSharp == Trueで各ピクセルが0か1に近いほど小さくなるような損失も追加
        sharpScore = 0
        if(self.sharpLoss is not None):
            sharpScore = self.sharpLoss(outputs)
            sharpScore *= self.onSharp
        rareScore = 0
        if(self.rareP is not None):
            rareScore = self.rareLoss(outputs, targets)
            rareScore *= self.rareP
            

        # outputsは正規化されていないので、正規化する
        outputs = transforms.Compose([
            transforms.Normalize(FontGeneratorDataset.IMAGE_MEAN, 
                FontGeneratorDataset.IMAGE_VAR)])(outputs)
        
        hingeLoss = 0
        if(self.hingeLoss is not None):
            hingeLoss = self.hingeLoss(outputs, targets)

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
        return torch.mean(ans) + sharpScore + rareScore + hingeLoss

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

class ImageHingeLoss(nn.Module):
    # 0より大小で間違っている時のみ2乗損失を加える
    # 正規化された後に入力すること
    def __init___(self):
        super().__init__()
    
    def forward(self, outputs, teachers):
        biggerT = torch.ge(teachers, 0.)
        smallerT = -1*torch.lt(teachers, 0.)
        biggerO = torch.ge(outputs, 0.) * outputs
        smallerO = torch.lt(outputs, 0.) * outputs
        ans = biggerT * (smallerO ** 2) + smallerT * (biggerO)*2
        return ans.mean()

class ImageRarePixelLoss(nn.Module):
    # 教師画像が白が多いときに結果に黒、黒が多いときに結果に白が出るほどロスが小さくなる

    #  正規化する前に入力すること

    UPPER_LIM = 0.8
    LOWER_LIM = 0.2

    def __init__(self, separateN = 1):
        super().__init__()
        self.separateN = separateN
    
    def getSectionLoss(self, reversedSize, outputs, teachers):
        uIndex = (teachers.sum(dim = (1, 2, 3)) >self.UPPER_LIM).broadcast_to(reversedSize).T
        uValue = torch.mul(torch.lt(teachers, self.LOWER_LIM), torch.square(outputs))
        uAns = torch.mul(uIndex, uValue).mean()
        lIndex = (teachers.sum(dim = (1, 2, 3)) <self.LOWER_LIM).broadcast_to(reversedSize).T
        lValue = torch.mul(torch.gt(teachers, self.UPPER_LIM), torch.square(outputs))
        lAns = torch.mul(lIndex, lValue).mean()
        return lAns + uAns

    
    def forward(self, outputs, teachers):
        size = tuple(teachers.size())
        reversedSize = tuple(reversed(size))
        if(self.separateN == 1):
            return self.getSectionLoss(reversedSize, outputs, teachers)
        else:
            size = (size[0], size[1], size[2]//self.separateN, size[3]//self.separateN)
            reversedSize = tuple(reversed(size))
            # ans = torch.zeros(1, device=outputs.device)
            splittedO1 = outputs.tensor_split(self.separateN, dim = 2)
            splittedT1 = teachers.tensor_split(self.separateN, dim = 2)
            # for i in range(self.separateN):
            #     splittedO2 = splittedO1[i].tensor_split(self.separateN, dim = 3)
            #     splittedT2 = splittedT1[i].tensor_split(self.separateN, dim = 3)
            #     ans += torch.stack([self.getSectionLoss(reversed, o, t) for o, t in zip(splittedO2, splittedT2)]).mean()
            # ans /= self.separateN 
            ans = torch.stack([
                torch.stack([self.getSectionLoss(reversedSize, o, t) for o, t in 
                    zip(o1.tensor_split(self.separateN, dim = 3), t1.tensor_split(self.separateN, dim = 3))])
                            for o1, t1 in zip(splittedO1, splittedT1)])
            ans = ans.mean()

            return ans