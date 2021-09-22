import os
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import matplotlib.pyplot as plt
import pickle
from ipywidgets import interact
import ipywidgets as widgets
from IPython.display import display
import random

class FontTools():
    ALPHANUMERICS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    SIGNS = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    KANA = "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろわをんァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロワヲン"
    JISCHINESECHARAS = ["JIS_first.txt", "JIS_second.txt"]
    FONTDIRS =  ["Fonts"]
    STANDARDFONT = "./msgothic.ttc"
    def __getJISList__():
        ans = []
        for path in FontTools.JISCHINESECHARAS:
            path = os.path.dirname(__file__) + "\\" + path
            data = ""
            with open(path, "r", encoding='UTF-8') as f:
                data = f.read()
                data = "".join(data.split("\n"))
            ans.append(data)
        return ans
    def __getFontCheckStrings__():
        fontCheckStrings = [FontTools.ALPHANUMERICS , FontTools.SIGNS, 
                        FontTools.KANA
                        ]
        fontCheckStrings += FontTools.__getJISList__()
        return fontCheckStrings
    def __init__(self):
        self.fontCheckStrings = FontTools.__getFontCheckStrings__()

    def getFontPathList():
        # Fontsフォルダ内のフォントファイルを一括取得
        dirs = FontTools.FONTDIRS

        l = []
        for d in dirs:
            for parent, _, filenames in os.walk(d):
                for name in filenames:
                    l.append(os.path.join(parent, name))
        return l

        
class CharacterChooser:
    # フォントのパスとそのフォントが対応している文字のリストを受け取り、ランダムに扱える文字をサンプリングする
    def __init__(self, fontTools: FontTools,  fontPath: str, compatibleList: list):
        self.fontTools = fontTools
        self.fontPath = fontPath
        self.compatibleList = compatibleList
        self.charaNList = [0 for i in range(len(fontTools.fontCheckStrings))] # カテゴリごとに文字がいくつあるかを累積数で示すリスト
        n = 0
        for i, (string, boolean) in enumerate(zip(fontTools.fontCheckStrings, compatibleList)):
            if boolean:
                n += len(string)
            self.charaNList[i] = n
        self.charaAllN = n
        self.special = compatibleList[-1] # 特殊なフォントはここがtrueになる
    
    def sample(self, sampleN: int):
        # このフォントが扱える文字の中から(最大)sampleN個サンプリングする
        sampleList = [i for i in range(self.charaAllN)]
        if(sampleN < self.charaAllN):
            # sampleNが、このフォントの対応文字数より少なかったら全部のペアを返す
            sampleList = random.sample(sampleList, sampleN)
        else:
            sampleN = self.charaAllN
        
        ans = [""] * sampleN
        for i, sampleInd in enumerate(sampleList):
            beforeN = 0
            for checkN, checkString in zip(self.charaNList, self.fontTools.fontCheckStrings):
                if(sampleInd < checkN):
                    ans[i] = checkString[sampleInd-beforeN]
                    break
                beforeN = checkN
        return ans


# 以下、集めたフォントの品質確認（漢字に対応するかなど）をするモジュール
class FontCheckImageProducer():
    # フォントを確認する用の画像を作るクラス

    maxCharas = 180 # チェック時に見る最大文字数
    charasHorizontalN = 30  # 横に並べる文字数    
    imageUnitSize = (600, 150)
    imageSize = (imageUnitSize[0]*2, imageUnitSize[1]*2)
    fontSize = 20
    
    def __init__(self, fontTools: FontTools):
        # 文字数が多いカテゴリはランダムにサンプリング
        self.fontPathList = FontTools.getFontPathList()
        self.fontCheckStrings = FontCheckImageProducer.__getFontCheckString__(fontTools)


        for i, string in enumerate(self.fontCheckStrings):
            if(len(string) > FontCheckImageProducer.maxCharas):
                sampled = random.sample(list(string), FontCheckImageProducer.maxCharas)
                string = "".join(sampled)
                self.fontCheckStrings[i] = string
            # 表示しやすいようにこの時点で整形
            horiN = FontCheckImageProducer.charasHorizontalN
            if(len(string) > horiN):
                strList = [string[horiN*i:horiN*(i+1)] for i in range(len(string) // horiN + 1)]
                string = "\n".join(strList)
                self.fontCheckStrings[i] = string

    def __getFontCheckString__(fontTools: FontTools):
        fontCheckStrings = fontTools.fontCheckStrings
        ans = [""] * 4
        ans[0] = fontCheckStrings[0] + fontCheckStrings[1]
        for i in range(1, 4):
            ans[i] = fontCheckStrings[i+1]
        return ans
    
    def getFontImage(self, ind):
        font_path = self.fontPathList[ind]
        font = PIL.ImageFont.truetype(font_path, FontCheckImageProducer.fontSize)

        image = PIL.Image.new('RGBA', FontCheckImageProducer.imageSize, 'white')
        draw = PIL.ImageDraw.Draw(image)
        for i, string in enumerate(self.fontCheckStrings):
            draw.text(((FontCheckImageProducer.imageUnitSize[0]) * (i % 2), (FontCheckImageProducer.imageUnitSize[1]) * (i // 2)),
                    string,
                    font=font,
                    fill='black')

        return image

class FontChecker():
    # フォントを表示しながら、どれに対応するかを記録していくGUI
    tagListIndex = ["英数字", "記号", "かな", "JIS第一水準", "JIS第二水準", "特殊"]
    checkListColumns = 3
    def __init__(self, fontCheckImageProducer):
        self.fontN = 0
        self.nowInd = -1
        self.fontList =  FontTools.getFontPathList()
        self.fontN = len(self.fontList)
        self.data = { i: [False for j in range(len(FontChecker.tagListIndex))] for i in self.fontList}
        self.fontCheckImageProducer = fontCheckImageProducer
    
    def __output__(self, ax, output):
        ax.clear()
        ax.imshow(self.fontCheckImageProducer.getFontImage(self.nowInd))
        with output:
            output.clear_output(wait=True)
            display(ax.figure)
    def __registerData__(self, checkBoxList):
        self.data[self.fontList[self.nowInd]] = [i.value for i in checkBoxList]
    
    def saveData(self, path):
        with open(path, "wb") as f:
            pickle.dump(self.data, f)

    def showWidgets(self):
        buttonNext = widgets.Button(description='Next')
        buttonPrev = widgets.Button(description='Prev')
        buttonSave = widgets.Button(description='Save')

        checkBoxList = [ widgets.Checkbox(value= False, description = i) for i in FontChecker.tagListIndex]
        
        output = widgets.Output()
        plt.figure(figsize = (100, 30))
        ax = plt.gca()


        def onClickNext(b: widgets.Button):
            if(self.nowInd == self.fontN-1):
                return
            self.__registerData__(checkBoxList)
            self.nowInd += 1
            self.__output__(ax, output)
        
        def onClickPrev(b: widgets.Button):
            if(self.nowInd == 0):
                return
            self.__registerData__(checkBoxList)
            self.nowInd -= 1
            self.__output__(ax, output)
        
        def onClickSave(b: widgets.Button):
            self.__registerData__(checkBoxList)
            self.saveData("checker.pkl")


        buttonNext.on_click(onClickNext)
        buttonPrev.on_click(onClickPrev)
        buttonSave.on_click(onClickSave)
        buttonBox = widgets.Box([buttonPrev, buttonNext, buttonSave])
        display(buttonBox)
        columns = FontChecker.checkListColumns
        for i in range(len(FontChecker.tagListIndex)//columns):
            box = widgets.Box(checkBoxList[i*columns: (i+1)*columns])
            display(box)
        display(output)

        plt.close()

        buttonNext.click()
