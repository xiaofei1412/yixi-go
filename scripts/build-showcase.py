"""Rebuild original documentation graphics; no training or engine calls.

Requires Pillow and matplotlib. Reads only the public results summary.
"""
from pathlib import Path
import argparse
import json
import os
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/assets'
OUT.mkdir(parents=True, exist_ok=True)
INK, GREEN, GOLD, PAPER = '#193a32', '#24745b', '#bd8d4e', '#f8f5ef'
FONT = 'Segoe UI,Microsoft YaHei,Noto Sans CJK SC,sans-serif'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--lang', choices=('zh', 'en'), default='zh')
LANG = parser.parse_args().lang


def label(zh, en):
    return en if LANG == 'en' else zh


def output(name):
    path = Path(name)
    return OUT / (f'{path.stem}-en{path.suffix}' if LANG == 'en' else name)


def text(x, y, value, size=24, color=INK, weight=400):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(value)}</text>'


def rect(x, y, w, h, color, radius=18, stroke='none'):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{color}" stroke="{stroke}"/>'


def svg(name, width, height, title, body):
    output(name).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img"><title>{escape(title)}</title><g font-family="{FONT}">{body}</g></svg>\n', encoding='utf-8')


# Original vector cover. The Go motif is decorative, not a product screenshot.
cover = rect(0, 0, 1200, 390, PAPER, 24)
cover += rect(38, 34, 52, 52, INK, 15) + text(47 if LANG == 'zh' else 54, 70, label('弈', 'Y'), 32, '#ffffff', 600)
cover += text(108, 58, 'YIXI  /  GO STUDY', 17, GREEN, 600)
cover += text(108, 82, label('工程实践 · 围棋学习', 'Engineering practice / Go learning'), 15, '#6d7970')
cover += text(48, 174, label('弈习', 'Yixi Go'), 66, INK, 700)
cover += text(48, 228, label('把每一局，变成下一次进步。', 'Turn every game into progress.'), 32, INK, 600)
cover += text(48, 275, label('KataGo 学习产品  ×  小预算模型训练', 'KataGo study workspace  ×  Small-budget training'), 24, GREEN)
cover += text(48, 338, 'PLAY  →  REVIEW  →  LEARN     /     DISTILL  →  PPO  →  EVALUATE', 15, '#69786f')
cover += rect(814, 27, 351, 336, '#eae5d8', 25)
for i in range(9):
    v = 847 + i*35
    cover += f'<path d="M{v} 55 V335 M847 {55+i*35} H1127" stroke="#c7c4b6" stroke-width="1"/>'
for c, r, white in [(2,2,0),(3,2,0),(4,2,0),(2,3,0),(3,3,1),(4,3,1),(5,3,1),(2,4,0),(3,4,1),(4,4,1),(5,4,0),(4,5,0),(5,5,0),(6,4,1)]:
    cover += f'<circle cx="{847+c*35}" cy="{55+r*35+2}" r="15.5" fill="#172b25" opacity=".12"/>'
    cover += f'<circle cx="{847+c*35}" cy="{55+r*35}" r="15.5" fill="{"#fffdf6" if white else INK}" stroke="{"#dad6ca" if white else INK}"/>'
cover += '<circle cx="1057" cy="230" r="14" fill="#24745b"/><circle cx="1057" cy="230" r="22" fill="none" stroke="#24745b" stroke-width="2" stroke-dasharray="4 5"/>'
svg('hero.svg',1200,390,label('弈习：KataGo 学习产品与小预算模型训练', 'Yixi Go: a KataGo study workspace and small-budget model training'),cover)

# Two routes with a shared, explicit local engine boundary.
body=rect(0,0,1200,335,PAPER,20)
body+=text(35,42,label('同一个围棋基础，两条实践路线','One Go foundation. Two engineering tracks.'),25,INK,600)
for y,route,color in [(84,label('产品路线','Product'),GREEN),(206,label('训练路线','Training'),GOLD)]:
    body+=text(35,y+30,route,21,color,600)
labels=([('Browser','Play / review / study'),('FastAPI + SQLite','Games / practice / cache'),('KataGo','Local analysis engine')]
        if LANG == 'en' else [('浏览器','对弈 · 复盘 · 学习中心'),('FastAPI + SQLite','棋谱 · 练习 · 缓存'),('KataGo','本地分析引擎')])
for i,(a,b) in enumerate(labels):
    x=165+i*340
    body+=rect(x,74,310,82,'#e5eee7',14)+text(x+20,106,a,23,INK,600)+text(x+20,134,b,17,'#596f61')
    if i<2:body+=text(x+316,122,'→',24,GREEN)
labels=([('Teacher data','200 games / 14,308 states'),('Distillation → PPO','320K parameters / 6 configs'),('Evaluate & select','1,200 + 400 evaluation games')]
        if LANG == 'en' else [('教师数据','200 局 / 14,308 状态'),('多任务蒸馏 → PPO','32 万参数 / 六组对照'),('验证与模型选择','1,200 局比较 + 400 局复核')])
for i,(a,b) in enumerate(labels):
    x=165+i*340
    body+=rect(x,196,310,82,'#efe6d6',14)+text(x+20,228,a,22,INK,600)+text(x+20,256,b,17,'#776849')
    if i<2:body+=text(x+316,242,'→',24,GOLD)
body+='<path d="M1015 158 V173 H320 V192" fill="none" stroke="#7b8e7a" stroke-width="1.5" stroke-dasharray="4 5"/>'
body+=text(35,314,label('共享规则与 SGF、引擎通信。学生模型用于实验；产品持续使用 KataGo。','Shared rules, SGF and engine I/O. The student model is experimental; the app uses KataGo.'),17,'#69786f')
svg('architecture.svg',1200,335,label('产品服务与模型训练的双路线架构','Separate product and model-training tracks'),body)

# A slow, finite animation explains the learning loop; first frame is complete.
font_path=os.environ.get('SHOWCASE_FONT')
if not font_path:
    windows_font=Path('C:/Windows/Fonts/segoeui.ttf' if LANG == 'en' else 'C:/Windows/Fonts/msyh.ttc')
    font_path=str(windows_font) if windows_font.exists() else font_manager.findfont('DejaVu Sans' if LANG == 'en' else 'Noto Sans CJK SC',fallback_to_default=False)
font=lambda size: ImageFont.truetype(font_path,size)
steps=(['Play / import','Pick a position','Try your move','Compare lines','Notes / revisit']
       if LANG == 'en' else ['对弈 / 导入','复盘精选','独立重下','三栏对照','笔记 / 复习'])
frames=[]
for active in [-1,0,1,2,3,4,-1]:
    im=Image.new('RGB',(1200,130),PAPER);d=ImageDraw.Draw(im)
    for i,step_label in enumerate(steps):
        x=24+i*238
        fill=GREEN if active==i else '#e5eee7'
        d.rounded_rectangle((x,25,x+200,101),radius=18,fill=fill)
        d.text((x+17,32),f'0{i+1}',font=font(15),fill='#cbdacf' if active==i else '#6d8273')
        d.text((x+17,57),step_label,font=font(22 if LANG == 'en' else 24),fill='white' if active==i else INK)
        if i<4:d.text((x+207,48),'→',font=font(25),fill=GREEN)
    frames.append(im)
frames[0].save(output('learning-loop.png'),optimize=True)
frames[0].save(output('learning-loop.gif'),save_all=True,append_images=frames[1:],duration=[900,800,800,800,800,800,1400],loop=1,optimize=True)

# Scientific chart: values/intervals come from the public audited JSON.
data=json.loads((ROOT/'docs/results-summary.json').read_text(encoding='utf-8'))
font_manager.fontManager.addfont(font_path)
plt.rcParams.update({'font.family':font_manager.FontProperties(fname=font_path).get_name(),'font.size':12,'axes.unicode_minus':False})
fig,ax=plt.subplots(figsize=(12,4.5),dpi=170,facecolor=PAPER)
ax.set_facecolor(PAPER)
names=(['8 games / round','32 games / round','Batch 512','Teacher replay 0.1','Value weight 0.1','Isolated critic']
       if LANG == 'en' else ['8 局 / 轮','32 局 / 轮','batch 512','教师监督 0.1','价值权重 0.1','独立 critic'])
for i,row in enumerate(data['common']):
    y=5-i;v=row['score_rate']*100;lo,hi=[n*100 for n in row['pair_bootstrap95']]
    color=GREEN if row['id']=='replay01' else GOLD if row['id']=='isolated' else '#8e9d94'
    ax.errorbar(v,y,xerr=[[v-lo],[hi-v]],fmt='o',color=color,elinewidth=3,capsize=5,markersize=8)
    ax.text(69,y,f'{v:.2f}%',va='center',color=INK,weight='bold',fontsize=13)
ax.axvline(50,color='#a2aaa3',ls='--',lw=1)
ax.set(yticks=range(5,-1,-1),yticklabels=names,xlim=(30,76),ylim=(-.7,5.7),xticks=[30,40,50,60,70])
ax.set_xticklabels(['30%','40%','50%','60%','70%'])
ax.tick_params(axis='both',length=0,pad=10,colors=INK)
for spine in ax.spines.values():spine.set_visible(False)
ax.grid(axis='x',alpha=.15)
fig.suptitle(label('六种 PPO 方案 · 同一批开局，各 200 局','Six PPO configurations · Shared openings, 200 games each'),x=.04,ha='left',fontsize=19,color=INK,weight='bold')
fig.text(.04,.035,label('点：教师裁定得分率   线：配对 95% 区间   虚线：50%   |   对手均为预训练基线，无 MCTS','Dots: teacher-adjudicated score rates   Lines: paired 95% intervals   Dashed: 50%   |   Pretrained baseline, no MCTS'),fontsize=10,color='#5c6c61')
fig.subplots_adjust(left=.24 if LANG == 'en' else .2,right=.95,top=.83,bottom=.15)
fig.savefig(output('experiments.png'),facecolor=PAPER)
plt.close(fig)

# Repository-local language buttons work in GitHub Markdown without scripts or CSS.
for code, name in [('en', 'English'), ('zh', '简体中文')]:
    body = rect(1,1,110,30,'#e5eee7',15,stroke='#cdd8cd')
    body += f'<text x="56" y="16" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="600" fill="{INK}">{name}</text>'
    (OUT/f'lang-{code}.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="112" height="32" viewBox="0 0 112 32" role="img"><title>{name}</title><g font-family="{FONT}">{body}</g></svg>\n',encoding='utf-8')
print(f'Built {LANG} hero, architecture, finite learning animation, results chart and language buttons.')
