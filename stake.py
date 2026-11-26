"""Recognize the max-bet controls in the normalized Merramun window."""
import cv2
import numpy as np
from vision import ROOT, load_image

class StakeControls:
    def __init__(self):
        self.templates={n:load_image(ROOT/'templates'/f'stake_{n}.png') for n in ('plus_on','plus_off','100','1')}

    def inspect(self,v):
        if not v.has('merr_title'):
            return 'hidden',None,{}
        mx,my,_,_=v.match('merr_title')[1]
        return self.inspect_panel(v.img,mx+322,my+748)

    def inspect_panel(self,img,cx,cy):
        def find(key,x,y,X,Y):
            if x<0 or y<0 or X>img.shape[1] or Y>img.shape[0]:return 0,None,None
            t=self.templates[key];h,w=t.shape[:2];a=img[y:Y,x:X]
            if a.shape[0]<h or a.shape[1]<w:return 0,None,None
            _,s,_,(px,py)=cv2.minMaxLoc(cv2.matchTemplate(a,t,cv2.TM_CCOEFF_NORMED))
            patch=a[py:py+h,px:px+w]
            return float(s),(x+px+w//2,y+py+h//2),patch
        on,p,a=find('plus_on',cx+55,cy-25,cx+115,cy+25)
        off,q,b=find('plus_off',cx+55,cy-25,cx+115,cy+25)
        maximum,_,_=find('100',cx-57,cy-24,cx+57,cy+24)
        one,_,_=find('1',cx-57,cy-24,cx+57,cy+24)
        if a is None:return 'hidden',None,{}
        hsv=cv2.cvtColor(a,cv2.COLOR_BGR2HSV)
        gold=float(((hsv[:,:,1]>95)&(hsv[:,:,2]>110)).mean())
        # 100.00 has six distinct white glyph groups, wider than 1..99.
        text=img[cy-10:cy+11,cx-50:cx+50]
        columns=(text.min(axis=2)>140).any(0)
        groups=int(np.count_nonzero(columns & ~np.r_[False,columns[:-1]]))
        xs=np.flatnonzero(columns);width=int(xs[-1]-xs[0]+1) if len(xs) else 0
        details={'glyphs':groups,'width':width,'max':round(maximum,3),'plus_on':round(on,3),'plus_off':round(off,3),'gold':round(gold,3),'one':round(one,3)}
        if maximum>=.82 and off>=.80 and gold<.22 and groups==6 and width>=53:return 'max',None,details
        if on>=.78 and gold>=.22:return 'raise',p,details
        if off>=.80 and gold<.22:return 'limited',None,details
        return 'hidden',None,details
