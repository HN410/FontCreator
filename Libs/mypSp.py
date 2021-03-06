
from Libs.myFontData import FontGeneratorDataset
from Libs.myFontLib import FontStyleChecker
import torch
import torch.nn as nn
import sys

from torchvision import transforms
sys.path.append('../')
from EfficientNet.model import *
from StyleGAN.network import *


# 画像を入力とし，それが何の文字かを判別する
# cycleGANを作るために実装
# ver 4 以降　モデルの表現力アップ
FEATURE_V_N = 256
class CharaDiscriminator(nn.Module):
    B3_CHANNEL_N = 384
    OUT_CHANNEL_N = 320
    def __init__(self, ver = 3):
        super().__init__()
        if(ver >= 4):
            blocks_args, global_params = get_model_params('efficientnet-b3', {})
            self.chara_encoder = EfficientNetEncoder(blocks_args, global_params, isForCharacter=True, ver=ver)
            load_pretrained_weights(self.chara_encoder, 'efficientnet-b3', weights_path=None,
                                load_fc=(ver < 2), advprop=False)
            self.last_conv = nn.Conv2d(in_channels=self.B3_CHANNEL_N, out_channels=self.OUT_CHANNEL_N, kernel_size=1)
            batch_norm_momentum=0.99
            batch_norm_epsilon=1e-3
            self.bn = nn.BatchNorm2d(num_features=self.OUT_CHANNEL_N, momentum=batch_norm_momentum, eps=batch_norm_epsilon)
        else:
            blocks_args, global_params = get_model_params('efficientnet-b0', {})
            self.chara_encoder = EfficientNetEncoder(blocks_args, global_params, isForCharacter=True, ver=ver)
            load_pretrained_weights(self.chara_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(ver < 2), advprop=False)
            self.last_conv = None
                            
        self.chara_encoder._change_in_channels(1)
    def forward(self, images):
        # teacherとなるmyPSPのself.chara_encoderの出力は
        # ほぼ正規化されている(mean ~ 0.075, var ~ 1.1)ので，正規化しなくてよい?
        # 1/10にしたほうがいい？
        ans = self.chara_encoder(images)
        if(self.last_conv is not None):
            return self.bn(self.last_conv(ans))
        else:
            return ans

# Styleの特徴量を入力として，それぞれの要素の値のテンソルで返す
class StyleDiscriminator(nn.Module):
    OUT_F_N = len(FontStyleChecker.defaultListIndex)
    IN_F_N = FEATURE_V_N * 2
    MID_F_N = [256, 128, 64, 32]
    DIVID_N = 4 
    USE_DIVID_N = 2 # 全結合層を使わない層数
    def __init__(self):
        # 全結合層を使う代わりに，特徴量をDIVID_N個の組に分けてそこでしばらく全結合させてみる
        super().__init__()
        self.activation = nn.LeakyReLU(negative_slope=0.2)
        self.linear1s = nn.ModuleList([nn.Linear(self.IN_F_N // self.DIVID_N, self.MID_F_N[0] // self.DIVID_N) for i in range(self.DIVID_N)])
        self.linear2s = nn.ModuleList([nn.Linear(self.MID_F_N[0] // self.DIVID_N, self.MID_F_N[1] // self.DIVID_N)for i in range(self.DIVID_N)])
        self.linear3s = nn.Linear(self.MID_F_N[1], self.MID_F_N[2])
        self.linear4s = nn.Linear(self.MID_F_N[2], self.MID_F_N[3])
        self.bn0 = nn.BatchNorm1d(self.MID_F_N[1])
        self.bn1 = nn.BatchNorm1d(self.OUT_F_N)
        self.last = nn.Linear(self.MID_F_N[3], self.OUT_F_N)
        self.lastActivation = nn.Sigmoid()

    def forward(self, features):
        # 入力　[B, teachersN, 256 * 2, 1, 1]
        # 出力  [B, len(list)]
        
        # teachersNで平均をとってしまう
        features = features.mean(1)[:, :, 0, 0]
        # 分解してそれぞれで計算
        features = torch.split(features, self.IN_F_N // self.DIVID_N, 1)
        features = [self.activation(self.linear1s[i](features[i])) for i in range(self.DIVID_N)]
        features = [self.linear2s[i](features[i]) for i in range(self.DIVID_N)]

        # 結合
        features = torch.cat(features, 1)
        if(features.shape[0] != 1):
            features = self.bn0(features)
        features = self.activation(features)
        
        features = self.activation(self.linear3s(features))
        features = self.activation(self.linear4s(features))

        raw = self.last(features)
        # if(features.shape[0] != 1):
        #     raw = self.bn1(raw)
        return self.lastActivation(raw), raw

class MyPSP(nn.Module):
    # 複数画像からフォントを構成するモデル
    def __init__(self, ver = 1, dropout_p = 0, useBNform2s = False, useBin = False):
        # chara_encoder ... どの文字かをエンコード
        # style_encoder ... 複数のフォントの組からスタイル情報をエンコード
        # style_gen ... エンコーダから得られた情報をもとにフォントを構成
        super().__init__()
        self.z_dim = FEATURE_V_N # エンコーダから渡される特徴量の個数
        blocks_args, global_params = get_model_params('efficientnet-b0', {})
        self.chara_encoder = EfficientNetEncoder(blocks_args, global_params, isForCharacter=True, ver=ver)
        self.style_encoder = EfficientNetEncoder(blocks_args, global_params, ver = ver, useBNform2s = useBNform2s)
        load_pretrained_weights(self.chara_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(ver < 2), advprop=False)
        load_pretrained_weights(self.style_encoder, 'efficientnet-b0', weights_path=None,
                                load_fc=(True), advprop=False)
        self.chara_encoder._change_in_channels(1)
        self.style_encoder._change_in_channels(1)
        gen_settings = get_setting_json()
        self.style_gen = Generator(gen_settings["network"], ver=ver, dropout_p=dropout_p)
        self.for_chara_training = False
        self.for_style_training = False
        self.ver = ver
        self.useBin = useBin
        self.Bin =BinarizationWithDerivative.apply
    def load_pretrained_weights_only_style(self):
        self.style_encoder._change_in_channels(3)
        load_pretrained_weights(self.style_encoder, 'efficientnet-b0', weights_path=None,
                        load_fc=(False), advprop=False)
        self.style_encoder._change_in_channels(1)
    
    
    def set_level(self, level):
        self.style_gen.set_level(level)
    
    def set_for_chara_training(self, b):
        # 文字のエンコードデコードのみを訓練するとき
        self.style_gen.set_for_chara_training(b)
        self.for_chara_training = b
    def set_for_style_training(self, b):
        # フォントのエンコードデコードのみを訓練するとき
        self.for_style_training = b
    
    def forward(self, chara_images,  style_pairs, alpha):
        # chara_image ... 変換したい文字のMSゴシック体の画像
        #   [B, 1, 256, 256]
        # style_pairs ... MSゴシック体の文字と、その文字に対応する変換先のフォントの文字の画像のペアのテンソル
        #   [B, pair_n, 2, 1, 256, 256]　→　 ver=4, [B, pair_n, 1, 256, 256] MSゴシック体をなくす
        # alpha ... どれだけ変化させるかの係数？バッチで共通なため、サイズは[1, 1]

        # 文字をエンコード [B, 256*6, 1, 1](ver1) or [B, 320, 8, 8](ver2)
        if(not self.for_style_training):
            chara_images = self.chara_encoder(chara_images)

        if self.for_chara_training:
            if self.ver >= 3:
                res = self.style_gen(chara_images, None, alpha)
                if(self.useBin):
                    res_ = self.Bin(res)
                    return chara_images, res, res_
                return chara_images, res
            else:
                return chara_images, torch.sigmoid(self.style_gen(chara_images, None, alpha))
        
        pair_n = style_pairs.size()[1]
        # ペアの差分をとる [B, pair_n, 1, 256, 256]
        if(self.ver <= 3):
            style_pairs = style_pairs[:, :, 1] -  style_pairs[:, :, 0]
        # 文字ごとにencoderにかけ、その特徴量を総和する [B, 256*2, 1, 1]
        style_pairs = torch.stack([self.style_encoder(style_pairs[:, i]) for i in range(pair_n)], dim = 1)
        if(self.for_style_training):
            return None, style_pairs,  None, None

        style_pairs_ = style_pairs.mean(1)


        res =  self.style_gen(chara_images, style_pairs_, alpha)
        if(self.useBin):
            res_ = self.Bin(res)
            return chara_images, style_pairs, res,  res_

        return chara_images, style_pairs,  res

class SoftCrossEntropy(nn.Module):
    eps = 1e-4
    def __init__(self, mean, std):
        super().__init__()
        self.mean = mean
        self.std = std
    def forward(self, out, teacher):
        teacher = teacher * self.std + self.mean
        out = teacher * torch.log(out + self.eps) + (1-teacher) * torch.log(1-out + self.eps)
        # if(torch.any(torch.isnan(out))):
        #     print("NAN")
        return -1 * out.mean()

class MyPSPLoss(nn.Module):
    # MyPSP用の損失関数
    # フォントは通常の画像と異なり、訓練画像とぴったり一致するほうがよいので、二乗誤差で試す
    # onSharpはImageSharpLossにかける係数

    MAIN_LOSS_N = 1
    SCALE = 2
    START_SCALE = 1
    FACTOR = 1

    # mode = mse, l1, crossE
    def __init__(self, mode = "mse", onSharp = 0, rareP = 0, separateN = 1, hingeLoss = 0):
        super().__init__()
        self.useNormalize = True
        self.mode = mode
        if(mode == "l1"):
            self.mainLoss = nn.ModuleList([nn.L1Loss() for i in range(self.MAIN_LOSS_N)])    
        elif(mode == "crossE"):
            self.mainLoss = nn.ModuleList([SoftCrossEntropy(FontGeneratorDataset.IMAGE_MEAN, FontGeneratorDataset.IMAGE_VAR) for i in range(self.MAIN_LOSS_N)])
            self.useNormalize = False
        else:
            self.mainLoss = nn.ModuleList([nn.MSELoss() for i in range(self.MAIN_LOSS_N)])
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
            
        hingeLoss = 0
        if(self.hingeLoss is not None):
            hingeLoss = self.hingeLoss(outputs, targets)

        # outputsは正規化されていないので、それに合わせる
        if(self.useNormalize):
            outputs = transforms.Compose([
                transforms.Normalize(FontGeneratorDataset.IMAGE_MEAN, 
                    FontGeneratorDataset.IMAGE_VAR)])(outputs)
        if(self.mode == "crossE"):
            targets = (targets * FontGeneratorDataset.IMAGE_VAR) + FontGeneratorDataset.IMAGE_MEAN
            

        ans = [0] * self.MAIN_LOSS_N

        # 1/8スタートで各SCALEでSCALE分の1してさらに誤差を計算
        factor = 1
        outputs = F.interpolate(outputs, scale_factor=1/self.START_SCALE, mode="bilinear")
        targets = F.interpolate(targets, scale_factor=1/self.START_SCALE, mode="bilinear")
        ans[0] = self.mainLoss[0](outputs, targets) * factor
        
        for i in range(self.MAIN_LOSS_N-1):
            factor *= self.FACTOR
            outputs = F.interpolate(outputs, scale_factor=1/self.SCALE, mode="bilinear")
            targets = F.interpolate(targets, scale_factor=1/self.SCALE, mode="bilinear")
            ans[i+1] = self.mainLoss[i+1](outputs, targets) * factor
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
    # 0より大小で間違っている時のみ損失を加える
    # 正規化される前に入力すること
    # teachers は正規化されていることに注意
    eps = 1e-3
    def __init___(self):
        super().__init__()
    
    def forward(self, outputs, teachers):
        biggerT = torch.ge(teachers, 0.)
        smallerT = torch.lt(teachers, 0.)
        biggerO = 1 - torch.ge(outputs, 0.5) * outputs # -log(1-x)
        smallerO = torch.lt(outputs, 0.5) * outputs + 1 # -log(x+1)
        ans = - 1* biggerT * torch.log(smallerO + self.eps) - smallerT * torch.log(biggerO + self.eps)
        return ans.mean()

class ImageRarePixelLoss(nn.Module):
    # 教師画像が白が多いときに結果に黒、黒が多いときに結果に白が出るほどロスが小さくなる

    #  正規化する前に入力すること
    TEACHER_LIM = 0.5
    UPPER_LIM = 0.7
    LOWER_LIM = 0.3
    eps = 1e-3

    def __init__(self, separateN = 1):
        super().__init__()
        self.separateN = separateN
    
    def getSectionLoss(self, reversedSize, outputs, teachers):
        uIndex = (teachers.mean(dim = (1, 2, 3)) >self.UPPER_LIM).broadcast_to(reversedSize).T
        uValue = torch.mul(torch.lt(teachers, self.LOWER_LIM), torch.log(1 - outputs + self.eps))
        uAns = torch.mul(uIndex, uValue).mean() # -log x
        lIndex = (teachers.mean(dim = (1, 2, 3)) <self.LOWER_LIM).broadcast_to(reversedSize).T
        lValue = torch.mul(torch.gt(teachers, self.UPPER_LIM), torch.log(outputs + self.eps))
        lAns = torch.mul(lIndex, lValue).mean() # -log(1-x)
        return -1 *lAns-uAns 

    
    def forward(self, outputs, teachers):
        teachers = teachers * FontGeneratorDataset.IMAGE_VAR + FontGeneratorDataset.IMAGE_MEAN
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

# 微分が伝わる二値化
# 出力で微分が大きいところは減らしたい，つまり1よりも0のほうが好ましい，微分が小さいところはその逆という仮定に基づく
# 二値化の代わりにヒンジのLeakyReLUを使った場合と似た挙動をする
class BinarizationWithDerivative(torch.autograd.Function):
    LEAKY_RELU_A = 0.002
    FACTOR = 1
    @staticmethod
    def forward(ctx, x):
        ans = (x > 0) +0.
        ctx.save_for_backward(x, ans)
        return ans
    
    @staticmethod
    def backward(ctx, dLdy):
        return dLdy * BinarizationWithDerivative.FACTOR
        # x, y = ctx.saved_tensors

        # dLB = (dLdy > 0) + 0. # 微分が大きかった
        # dLS = 1-dLB # 微分が小さい

        # # print(x.max(), x.min(), x.mean(), x.var())
        # # print(dLdy.max(), dLdy.min(), dLdy.mean(), dLdy.var())

        # dLB = dLB * dLdy
        # dLS = dLS * dLdy * -1

        # reduce = dLB * y - dLS * (1-y) # 増やしたいのに0のところ，減らしたいのに1のところ
        # same = dLB * (1-y) - dLS * y # あっているところは少しだけよりその方向に向かうように

        # return (reduce  + same * BinarizationWithDerivative.LEAKY_RELU_A) * BinarizationWithDerivative.FACTOR


# style encoderから出力される値で損失をとる
# feature ... [B, teacher_n 256*2, 1, 1]
STYLE_LOSS_SAME_FACTOR = 1
def styleLoss(feature):
    teacher_n = feature.size()[1]
    Bn = feature.size()[0]
    loss = 0
    if(teacher_n > 1):
        # 同じフォントは同じ値が出るように
        sep = (teacher_n+ 1) // 2
        feature0 = feature[:,0:sep+1].mean(1)
        feature1 = feature[:, sep:teacher_n].mean(1)
        loss = loss + STYLE_LOSS_SAME_FACTOR * torch.nn.MSELoss()(feature0, feature1)
        del feature0, feature1
    if( feature.size()[1] > 1):
        # 違うフォントからは違う値が出るように
        # あまり遠い値が出ないように-log(sum(|f-t|))とする
        feature = feature.mean(1)
        loss  = loss - torch.log(torch.nn.L1Loss()(feature, feature[list(range(Bn))[::-1]])+ 1e-1)
    return loss

