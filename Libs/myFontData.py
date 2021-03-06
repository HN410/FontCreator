import random
import torch
from torchvision.transforms.transforms import Grayscale
from .myFontLib import *
import torch.utils.data as data
from torchvision.transforms import functional as tvf
import numpy as np
import torch.nn.functional as F
import cv2

class FontGeneratorDataset(data.Dataset):
    # ゴシック体と各フォントのペア画像の組を出力するデータセット

    # 平均、分散を計算するときにサンプリングする文字数、フォント数
    MEANVAR_CHARA_N = 40
    MEANVAR_FONT_N = 70

    IMAGE_MEAN = 0.8893
    IMAGE_VAR = 0.0966

    IMAGE_WH = 256

    def __init__(self, fontTools: FontTools, compatibleDict: dict, imageN : list, styleDict: dict,\
         useTensor=True, startInd = 0, indN = None, isForValid = None, augmentationP = None, originalAugmentationP = None,):
        #  fontTools ... FontTools
        #  compatibleDict ... 各フォントごとに対応している文字のリストを紐づけたディクショナリ
        #  imageN ... ペア画像を出力する数の範囲(要素は２つ)
        #             (例) [3, 6] ... 3~6個の中から一様分布で決定される
        #                  [4, 4] ...4個で固定
        #  startInd ... fontListのうち、このインデックス以降のフォントのみを使う
        #  indN ...startIndからindN個のフォントのみを使う。NoneならstartInd以降すべて
        #  isForValid ... validationなどで、常に固定したデータで出力をしたいときに使う
        # 　　getInputListForVで取得したディクショナリをここに入れればよい。
        #  augmentationP ... オーグメンテーションをする確率。Noneなら0, floatの二次元リストを受け取る
        self.fontTools = fontTools
        self.fontList = FontTools.getFontPathList()
        self.compatibleDict = compatibleDict
        self.styleDict = styleDict
        self.imageN = imageN
        self.resetSampleN()
        self.useTensor = useTensor
        self.startInd = startInd
        if(indN is None):
            self.indN = len(self.fontList) - startInd
        else:
            self.indN = indN

        self.normalize = transforms.Compose([
            transforms.Normalize(self.IMAGE_MEAN, self.IMAGE_VAR)
        ])
        if(isForValid is not None):
            self.isForValid = True
            self.fixedInput = isForValid
        else:
            self.isForValid = False
        
        self.augmentationP = augmentationP
        self.originalAugmentationP = originalAugmentationP
        

    def __len__(self):
        return self.indN
    
    def __getitem__(self, index):
        # 形式は変換用の画像の組と教師用データのテンソルのリスト
        # 変換用画像 idx:0 [1, 256, 256]の変換元画像
        #           idx:1 [1, 256, 256]の変換後画像
        # 教師用データ [imageN-1, 2, 1, 256, 256]のゴシック、変換後フォントの文字の画像のペアのテンソル
        

        # まず、入力されたindexを補正
        index += self.startInd

        imageList = []

        charaChooser = CharacterChooser(self.fontTools, self.fontList[index],
                self.compatibleDict[self.fontList[index]], useTensor=self.useTensor)
        beforeNormalize= None
        styleChangeList0 = [False, False] # 非正方形, ノイズ
        styleChangeList1 = [False] * OriginalAugSet.TRANSFORM_N
        if(self.augmentationP is not  None):

            beforeNormalize, styleChangeList = MyPSPAugmentation.getTransform(self.IMAGE_WH, self.augmentationP)
        if(self.originalAugmentationP):
            if(beforeNormalize):
                beforeNormalize = [beforeNormalize]
            else:
                beforeNormalize =[]
            aug, styleChangeList1 = OriginalAugSet.getAll(self.originalAugmentationP)
            if(aug):
                beforeNormalize.append(aug)
            beforeNormalize = transforms.Compose(beforeNormalize)

        else:
            if(beforeNormalize is not None):
                beforeNormalize = transforms.Compose([beforeNormalize])

        if(self.isForValid):
            imageList = charaChooser.getImageFromSampleList(self.fixedInput[index], self.normalize, beforeNormalize)
        else:
            sampleN = self.sampleN
            imageList = charaChooser.getSampledImagePair(sampleN, self.normalize, beforeNormalize)

        convertedPair = imageList[0]
        teachers = torch.stack([torch.stack(i, 0) for i in imageList[1:]], 0)

        # Style情報
        styleLabel = torch.tensor(self.styleDict[self.fontList[index]])
        styleLabel = self.getModifiedStyleLabel(styleLabel, styleChangeList0, styleChangeList1)
        
        return [convertedPair, teachers, styleLabel]

    @classmethod
    def getModifiedStyleLabel(cls, label, changeList0, changeList1):
        # augmentationで変わった分ラベルも修正する
        if(changeList0[0]):
            # アフィン変換など
            label[13] = min(label[13] + 0.2, 1.0)
        if(changeList0[1]):
            # ノイズ
            label[12] = min(label[12] + 0.2, 1.0)
        if(changeList1[0]):
            # ラプラシアン
            label[8] = 1.0
        if(changeList1[1]):
            # 膨張
            label[0] = min(label[0] + 0.1, 1.0)
        if(changeList1[2]):
            # 収縮
            label[0] = max(label[0] - 0.1, 0.0)
        if(changeList1[3]):
            # line
            label[11] = min(label[11] + 0.6, 1.0)
            label[10] = min(label[10] + 0.2, 1.0)
        if(changeList1[4]):
            # circle
            label[12] = min(label[12] + 0.5, 1.0)
        if(changeList1[5]):
            # noise
            label[12] = min(label[12] + 0.2, 1.0)
        if(changeList1[6]):
            # circle
            label[6] = min(label[6] + 0.2, 1.0)
            label[7] = max(label[7] - 0.2, 0.0)
            label[15] = min(label[15] + 0.2, 1.0)
        return label
    
    def getInputListForV(self):
        # validationように常に固定された入力が出るよう、このデータセットに設定するディクショナリを作る
        # 形式は、フォントのインデックスをキーとする文字のリストのディクショナリ
        sampleN = random.randint(self.imageN[0], self.imageN[1])
        ans = {}
        for i in range(self.__len__()):
            charaChooser = CharacterChooser(self.fontTools, self.fontList[self.startInd+ i],
                 self.compatibleDict[self.fontList[self.startInd + i]])
            ans[i] = charaChooser.sample(sampleN)
        return ans

    def getJapaneseFontIndices(self):
        # 日本語の文字を含むフォントに対応するインデックスのリストを返す
        index = self.startInd
        ans = []
        for i in range(self.indN):
            font = self.fontList[index]
            compatibleList = self.compatibleDict[font]
            for j in range(2, 5):
                if compatibleList[j]:
                    ans.append(i)
                    break
            index+=1
            i+=1
        return ans 


    @classmethod
    def getCharaImagesMeanVar(cls, compatibleData, isMinus = False):
        # フォント画像の平均、分散を得る

        fontDataSet = FontGeneratorDataset(FontTools(), compatibleData, [cls.MEANVAR_CHARA_N, cls.MEANVAR_CHARA_N], useTensor=True)
        fontDataSet = iter(fontDataSet)
        # [FONT_N, CHARA_N, 2, 1, 256, 256]
        data = torch.cat([torch.cat([torch.cat(j, 0) for j in list(fontDataSet.__next__())]) for i in range(cls.MEANVAR_FONT_N)])
        mean = torch.mean(data).item()
        var = torch.var(data).item()
        return mean, var
    
    def resetSampleN(self):
        self.sampleN = random.randint(self.imageN[0], self.imageN[1])


class MyPSPCharaDataset(data.Dataset):
    # 文字のエンコード訓練用
    def __init__(self, charaList):
        # charaList ... 画像を作りたい文字のリスト
        self.charaList = charaList
        self.transform = transforms.Compose([ 
            transforms.Grayscale(), 
            transforms.ToTensor(), 
            transforms.Normalize(FontGeneratorDataset.IMAGE_MEAN,
                                     FontGeneratorDataset.IMAGE_VAR)
        ])

    def __len__(self):
        return len(self.charaList)


    def __getitem__(self, index):
        # 変換した画像が帰ってくる

        # まず、入力されたindexを補正
        image = self.transform(CharacterChooser.__getImage__(FontTools.STANDARDFONT, 
                                                            self.charaList[index]))
        image.view(1, 256, 256)
        return image


class MyPSPBatchSampler(torch.utils.data.sampler.BatchSampler):
    # MyPSP用のBatchSampler
    def __init__(self, batchSize, fontGeneratorDataset: FontGeneratorDataset, japaneseRate = 0):
        self.fontGeneratorDataset = fontGeneratorDataset
        self.len = len(fontGeneratorDataset)
        self.batchSize = batchSize
        if( 0 < japaneseRate <= 1):
            self.japaneseRate = japaneseRate
        else:
            self.japaneseRate = 0
        
    def __iter__(self):
        self.count = self.batchSize
        self.indicesList = random.sample(list(range(self.len)), self.len)
        if(self.japaneseRate > 0):
            self.japaneseIndicesList = random.choices(self.fontGeneratorDataset.getJapaneseFontIndices(), k=self.len)
        while self.count <= self.len:
            self.fontGeneratorDataset.resetSampleN()
            if(random.random() < self.japaneseRate):
                yield(self.japaneseIndicesList[self.count-self.batchSize: self.count])
            else:
                yield(self.indicesList[self.count-self.batchSize: self.count])
            self.count += self.batchSize
    
    def __len__(self):
        return self.len // self.batchSize


class MyPSPAugmentation:
    ROTATE_LIMIT = 15
    TRANSLATE_LIMIT = 5
    SCALE_LIMIT = 0.1
    PERSPECTIVE_LIMIT = 0.1
    NOISE_STRENGTH = 0.001

    @classmethod
    def getTransform(cls, imageWH, probs, device = "cpu"):
        #画像，確率を受け取って変形した画像，[変形したか, ノイズが載ったか]どうかを返す
        useAffine = random.random() < probs[0]
        usePerspective = random.random() < probs[1]
        useNoise = random.random() < probs[2]
        if(not(useAffine or usePerspective or useNoise)):
            return None, [False, False]
        angle =  translate =  scale = shear =  interpolation = None
        if(useAffine):
            angle = random.uniform(-1*cls.ROTATE_LIMIT, cls.ROTATE_LIMIT)
            translate = [random.uniform(-1*cls.TRANSLATE_LIMIT, cls.TRANSLATE_LIMIT),
                             random.uniform(-1*cls.TRANSLATE_LIMIT, cls.TRANSLATE_LIMIT)]
            scale = random.uniform(1-2*cls.SCALE_LIMIT, 1+cls.SCALE_LIMIT)
            shear = [random.uniform(-1*cls.SCALE_LIMIT, cls.SCALE_LIMIT), random.uniform(-1*cls.SCALE_LIMIT, cls.SCALE_LIMIT)]
            interpolation = tvf.InterpolationMode.BILINEAR if random.random() > 0.5 else \
                                tvf.InterpolationMode.NEAREST
        startPoints = endPoints = None
        if(usePerspective):
            limit = int(cls.PERSPECTIVE_LIMIT * imageWH) // 2
            startPoints = [[0+random.randint(-1*limit, limit), 0+random.randint(-1*limit, limit)], 
                            [0+random.randint(-1*limit, limit), imageWH+random.randint(-1*limit, limit)], 
                            [imageWH+random.randint(-1*limit, limit), 0+random.randint(-1*limit, limit)], 
                            [imageWH+random.randint(-1*limit, limit), imageWH+random.randint(-1*limit, limit)]]
            endPoints = [[0+random.randint(-1*limit, 2*limit), 0+random.randint(-1*limit, 2*limit)], 
                            [0+random.randint(-1*limit, 2*limit), imageWH+random.randint(-2*limit, limit)], 
                            [imageWH+random.randint(-2*limit, limit), 0+random.randint(-1*limit, 2*limit)], 
                            [imageWH+random.randint(-2*limit, limit), imageWH+random.randint(-2*limit, limit)]]
        def transpose(img):
            if(useAffine  or usePerspective):
                img = 1-img
                if(useAffine):
                    img = tvf.affine(img, angle, translate, scale, shear, interpolation, fill = [0])
                if(usePerspective):
                    img = tvf.perspective(img, startPoints, endPoints, fill = [0])
                img = 1-img
            if(useNoise):
                size = img.size()
                new  = img+ 20*cls.NOISE_STRENGTH* torch.randn(size, device=device)
                return new
            return img
        return transforms.Lambda(transpose), [useAffine or usePerspective, useNoise]
    
    @classmethod
    def getNoisedImages(cls, data, prob, device = "cpu"):
        # データ郡を受け取って、確率でノイズの入ったデータ(とノイズが載ったか)を返す
        # teachers [B, teachersN, 1, 256, 256]
        transform, boolList = cls.getTransform(256, [0, 0, prob], device)
        if(transform is None):
            return data
        ans = []
        for e in data:
            ans.append(transform(e))

        return ans, boolList[1]
        
# augmentation で，入力されるimgはshapeが[1, 1, 356, 256] だったり，[1, 256, 256]だったりするので注意
class ConvAugmentation:
    Laplacian = torch.tensor([[[[1., 1., 1.], [1., -8., 1.], [1., 1., 1.]]]])
    # Sobel = torch.tensor([[[[-1., 0., 1.], [-2., 0., 2.], [-1., 0., 1.]]]])
    @classmethod
    def getTransformWithKernel(cls, kernel, device):
        kernel = kernel.to(device)
        def transform(img):
            img = 1-img.unsqueeze(0)
            return  0.1 +  F.conv2d(img, kernel, padding = "same")[0]
        return transforms.Lambda(transform)
    
    @classmethod
    def getTransformRandom(cls, device):
        return cls.getTransformWithKernel(cls.Laplacian,device)
        # if(random.random() < 0.5):
        # else:
        #     return cls.getTransformWithKernel(cls.Sobel,device)
#模様をつける
class PatteringAugmentation:
    #線の模様用のnumpy array 
    # size = 画像の幅
    # lineNumRange = 線の本数の範囲
    # lineWidthRange = 線の太さの範囲
    @classmethod
    def getLineArray(cls, size, lineNumRange = [8, 40], lineWidthRange = [2,4]):

        img = np.zeros((size, size))
        lineLength = 1.5 * size / 2 # 直線の長さの1/2
        center = np.array([size / 2, size / 2])
        theta = np.pi *  random.random() 
        startBase = center + lineLength * np.array([-1 * np.sin(theta),  np.cos(theta)])
        endBase = center - lineLength * np.array([-1 * np.sin(theta), np.cos(theta)])
        moveBase = 2 * np.array([lineLength * np.cos(theta), lineLength * np.sin(theta)])
        for i in range(random.randint(lineNumRange[0], lineNumRange[1])):
            move = moveBase * (random.random() - 0.5)
            start = move + startBase 
            end   = move + endBase
            cv2.line(img, (int(start[0]), int(start[1])), (int(end[0]), int(end[1])), (1, 1 , 1),
                thickness = random.randint(lineWidthRange[0], lineWidthRange[1]))
        return img

    @classmethod
    def getCircleArray(cls, size, circleNumRange = [5, 60], radiusRange = [2, 7]):
        img = np.zeros((size, size))

        for i in range(random.randint(circleNumRange[0], circleNumRange[1])):
            (x, y) = (random.randint(0, size), random.randint(0, size))
            r = random.randint(radiusRange[0],radiusRange[1])
            cv2.circle(img, (x, y),r, (1, 1, 1), thickness = -1)
        return img
    
    @classmethod
    def getNoiseArray(cls, size, strengthRange = [0.6, 2]):
        img = np.abs(np.random.randn(size, size))
        img =  (img > random.uniform(strengthRange[0], strengthRange[1])) + 0.0
        return img

    # @classmethod
    # def getLinePaint(cls, w, device = "cpu"):
    #     lineImg = cls.getLineArray(w).reshape((1, w, w))
    #     lineImg = torch.tensor(lineImg, device = device)
    #     def getLinedImg(img):
    #         return 10 * lineImg + img
    #     return getLinedImg
    
    @classmethod
    def getPaintAugmentation(cls, w, mode, device = "cpu"):
        if(mode == "line"):
            patternImg = cls.getLineArray(w).reshape((1, w, w))
        elif(mode == "circle"):
            patternImg = cls.getCircleArray(w).reshape((1, w, w))
        elif(mode == "noise"):
            patternImg = cls.getNoiseArray(w).reshape((1, w, w))
        else:
            raise NotImplementedError
        patternImg = torch.tensor(patternImg, device = device)
        def getImg(img):
            return 10 * patternImg + img
        return getImg

# 画像をゆがめるタイプ    
class DistortingAugmentation:
    @staticmethod    
    def getWaveF(k1, k2, c1, c2, l1, l2):
        def f(x):
            w1 = l1 * np.sin(k1 * x + c1)
            w2 = l2 * np.sin(k2 * x + c2)
            return w1 + w2
        return f
    
    @classmethod
    def getWaving(cls, size):
        MaxL = size / 50
        padN = int(MaxL)*2 + 1
        k1 = np.pi*(1 + random.random() * 15) / size
        k2 = np.pi*(1 + random.random() * 15) / size
        l1 = MaxL * random.random()
        l2 = MaxL * random.random()
        trans = random.random() > 0.5
        def f(img):
            shape = img.shape
            c1 = size * random.random()
            c2 = size * random.random()

            waveF = cls.getWaveF(k1, k2, c1, c2, l1, l2)
            img = img.reshape((size, size))
            padded = F.pad(img, (padN, padN, padN, padN), value = 1.0)

            if(trans):
                padded = padded.T
            img = torch.stack([padded[i][padN + int(waveF(i)) : padN + int(waveF(i)) + size] for i in range(padN, padN + size)])
            if(trans):
                img = img.T
            return img.reshape(shape)
        return transforms.Lambda(f)
    

class OriginalAugSet:
    TRANSFORM_N = 7
    @classmethod
    def getBinarization(cls,val = 0.0):
        def bin(img):
            return ((img > val) + 0.)
        return transforms.Lambda(bin)
    

    # モルフォロジー変換で収縮(フォントは文字部分が0)
    # @classmethod
    # def contract(cls,  img, size = 1):
        
    @classmethod 
    def getContract(cls, size):
        def contract(img):
            kernelsize = (size * 2 + 1)
            img = img.unsqueeze(0)
            return torch.nn.functional.max_pool2d(img, (kernelsize, kernelsize), stride = 1 , padding = size)[0]
        return transforms.Lambda(contract)

    @classmethod 
    def getExpand(cls, size):
        def expand(img):
            kernelsize = (size * 2 + 1)
            img = img.unsqueeze(0)
            return -1 * torch.nn.functional.max_pool2d(-1 * img, (kernelsize, kernelsize), stride = 1 , padding = size)[0]
        return transforms.Lambda(expand)
    
    # すべてを組み合わせたtransformとtransformしたかのリストを返す
    # pList ... それぞれが適用される確率
    # [ラプラス, expand or contract, line, circle, noise, wave]
    # boolListは上のうち，expand, contractになったもの
    @classmethod
    def getAll(cls, pList, size = 256, device = "cpu"):
        ans = []
        useLaplace = pList[0] > random.random()
        boolList = [False] * cls.TRANSFORM_N
        if(useLaplace):
            ans.append(ConvAugmentation.getTransformRandom(device))
            boolList[0] = True
        if(pList[1] > random.random()):
            if(0.5 >random.random() and (not useLaplace)):
                ans.append(cls.getContract(1))
                boolList[2] = True
            else:
                ans.append(cls.getExpand(random.randint(1, 3)))
                boolList[1] = True
        if(pList[2] > random.random()):
            ans.append(PatteringAugmentation.getPaintAugmentation(size, "line", device))
            boolList[3] = True
        if(pList[3] > random.random() and (not useLaplace)):
            ans.append(PatteringAugmentation.getPaintAugmentation(size, "circle", device))
            boolList[4] = True
        if(pList[4] > random.random() and (not useLaplace)):
            ans.append(PatteringAugmentation.getPaintAugmentation(size, "noise", device))
            boolList[5] = True
        if(pList[5] > random.random()):
            ans.append(DistortingAugmentation.getWaving(size))
            boolList[6] = True

        if(ans !=  []):
            ans.append(cls.getBinarization())
            return transforms.Compose(ans), boolList
        else:
            return None, boolList

    
        