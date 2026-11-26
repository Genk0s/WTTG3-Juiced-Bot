from pathlib import Path
import json, re, cv2, numpy as np
ROOT=Path(__file__).resolve().parent
SIZE=(1650,927)
def load_image(path,flags=cv2.IMREAD_COLOR):
    return cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),flags)
class Vision:
    def __init__(self):
        self.spec=json.loads((ROOT/'templates.json').read_text(encoding='utf-8'))
        self.templates={k:load_image(ROOT/'templates'/f'{k}.png') for k in self.spec if k!='digits'}
        self.awesome=load_image(ROOT/'templates'/'win_awesome.png',cv2.IMREAD_GRAYSCALE)
        self.bank={k:[self.norm(load_image(ROOT/'templates'/f,0)>0) for f in fs] for k,fs in self.spec['digits'].items()}
    def set(self,img):
        self.img=cv2.resize(img,SIZE,interpolation=cv2.INTER_AREA); self.cache={}; self._overwrite_cache=None; self._danger_cached=False; self._danger_value=None; return self
    def match(self,k):
        if k in self.cache:return self.cache[k]
        t=self.templates[k]; x,y,X,Y=self.spec[k]['roi']; crop=self.img[y:Y,x:X]
        a=cv2.matchTemplate(crop,t,cv2.TM_CCOEFF_NORMED); _,score,_,p=cv2.minMaxLoc(a)
        v=(score,(x+p[0],y+p[1],t.shape[1],t.shape[0])); self.cache[k]=v;return v
    def has(self,k):return self.match(k)[0]>=self.spec[k]['threshold']
    def pos(self,k):
        x,y,w,h=self.match(k)[1];return x+w//2,y+h//2
    def on_desktop(self):
        # The saved desktop can have different wallpaper or other apps open.
        # Merramun itself or its icon plus a bottom-bar marker is also evidence.
        return (self.has('desktop') or self.has('merr_title') or
                (self.has('merr_icon') and
                 (self.has('bar_yolo') or self.has('bar_dos') or self.has('bar_phone') or self.has('pm'))))
    def desk_prompt(self):
        if self.has('enter_desk'):return 'enter_desk'
        if self.has('sit_desk'):return 'sit_desk'
        return None
    def spin_state(self):
        k=max(('spin','spin_hover','disabled'),key=lambda name:self.match(name)[0])
        score,r=self.match(k)
        if score<self.spec[k]['threshold']:return None
        x,y,w,h=r
        c=self.img[y:y+h,x:x+w];hsv=cv2.cvtColor(c,cv2.COLOR_BGR2HSV)
        gold=float(np.mean((hsv[:,:,1]>120)&(hsv[:,:,2]>140)))
        if k in ('spin','spin_hover') and gold>.20:return 'spin'
        if k=='disabled' and gold<.12:return 'disabled'
        return None
    def spin_pos(self):
        if self.spin_state()!='spin':raise ValueError('Spin button is not active')
        return self.pos(max(('spin','spin_hover'),key=lambda name:self.match(name)[0]))
    @staticmethod
    def glyphs(crop):
        g=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY); mask=(g>145).astype('uint8')
        active=mask.any(0); seg=[]; start=None
        for x,a in enumerate(np.r_[active,False]):
            if a and start is None:start=x
            elif not a and start is not None:
                m=mask[:,start:x];ys=np.flatnonzero(m.any(1))
                if len(ys):seg.append(m[ys[0]:ys[-1]+1])
                start=None
        return seg
    @staticmethod
    def norm(g):return cv2.resize(g.astype('float32'),(12,18),interpolation=cv2.INTER_LINEAR)
    def digit(self,g):
        if g.shape[0]<5 and g.shape[1]<5:return '.'
        if g.shape[0]<8 or g.shape[0]>22:return None
        a=self.norm(g)
        ranked=sorted((min(float(np.mean(np.abs(a-b))) for b in bs),d) for d,bs in self.bank.items())
        if ranked[0][0]>.28 or ranked[1][0]-ranked[0][0]<.025:return None
        return ranked[0][1]
    def readnum(self,crop):
        gs=self.glyphs(crop)
        chars=[self.digit(g) for g in gs]
        if not chars or None in chars:return None
        text=''.join(chars)
        try:return float(text)
        except ValueError:return None
    def readmoney(self,crop):
        if crop.size==0:return None
        chars=[self.digit(g) for g in self.glyphs(crop)]
        if not chars or None in chars:return None
        text=''.join(chars)
        if not re.fullmatch(r'[0-9]{1,8}\.[0-9]{2}',text):return None
        return float(text)
    def bithit_balances(self):
        if not self.has('bit_title'):return None,None
        x,y,_,_=self.match('bit_title')[1]
        return (self.readmoney(self.img[y+36:y+59,x+105:x+185]),
                self.readmoney(self.img[y+36:y+59,x+221:x+365]))
    def taskbar_balances(self):
        if not all(self.has(k) for k in ('bar_dos','bar_yolo','bar_phone')):return None,None
        dx,dy,dw,dh=self.match('bar_dos')[1];yx,yy,yw,yh=self.match('bar_yolo')[1];px,py,pw,ph=self.match('bar_phone')[1]
        if not (yx+yw+20<dx and dx+dw+20<px and abs(dy-yy)<=2):return None,None
        return (self.readmoney(self.img[898:919,dx+dw:px-3]),
                self.readmoney(self.img[898:919,yx+yw:dx-3]))
    def balances(self):
        # Taskbar is always available, even with BitHit closed. Only a
        # positive balance can be used as a fallback for ordinary clicking.
        td,ty=self.taskbar_balances();bd,by=self.bithit_balances()
        return (td if td is not None else bd,ty if ty is not None else by)
    def slot_frame(self):
        if not self.has('merr_title'):return False
        mx,my,_,_=self.match('merr_title')[1]
        for k,ox,oy in [('slot_corner',614,95),('slot_lower_corner',611,568)]:
            t=self.templates[k];h,w=t.shape[:2];x,y=mx+ox,my+oy
            if x<3 or y<3 or x+w+3>1650 or y+h+3>927:return False
            crop=self.img[y-3:y+h+3,x-3:x+w+3]
            if cv2.matchTemplate(crop,t,cv2.TM_CCOEFF_NORMED).max()<.80:return False
        # The reel panel must remain visible; a black screen or a large
        # dimmed overlay is not a normal transition.
        board=self.img[my+135:my+585,mx+25:mx+630]
        if not board.size:return False
        return float(board.mean())>25
    def loss_confirmed(self):
        # A genuine MAX LOSS has large red lettering. A gold MAX WIN
        # banner must not be mistaken for it merely because of its shape.
        score,(x,y,w,h)=self.match('loss')
        if score<self.spec['loss']['threshold']:return False
        b,g,r=cv2.split(self.img[y:y+h,x:x+w].astype(np.float32))
        return float(((r>110)&(r>g*1.35)&(r>b*1.5)).mean())>.12

    def payout_info(self, previous_anchor=None):
        # The titlebar can be dimmed during the award. A relaxed title
        # match is only accepted near the last verified title AND with
        # direct award evidence. Never infer a click target from gold alone.
        score,(mx,my,_,_)=self.match('merr_title')
        strong=score>=self.spec['merr_title']['threshold']
        near=(previous_anchor is not None and score>=.69 and
              abs(mx-previous_anchor[0])<=18 and abs(my-previous_anchor[1])<=18)
        if not (strong or near):return None,None
        x,y=(mx,my) if strong else previous_anchor
        if not (x>=0 and y>=0 and x+610<=1650 and y+700<=927):return None,None
        if self.loss_confirmed():return None,None
        if strong and self.has('tumble'):return 'tumble',(x,y)
        # The payout plaque is a wide band of gold/orange, not the small
        # round spin control, thin reel border, or red MAX LOSS plaque.
        crop=self.img[y+505:min(y+796,927),x+40:x+610]
        h,s,val=cv2.split(cv2.cvtColor(crop,cv2.COLOR_BGR2HSV))
        gold=(h>=8)&(h<=38)&(s>100)&(val>100)
        wide=gold.mean(axis=1)>.40
        if int(wide.sum())>=18:return 'gold_payout',(x,y)
        if strong and (self.spin_state() in ('spin','disabled') or self.has('better')):return None,None
        if self.awesome is not None:
            crop=cv2.cvtColor(self.img[y+210:min(y+430,927),x+50:x+610],cv2.COLOR_BGR2GRAY)
            for scale in (1.02,1.,.98,1.04,.94,1.08,.90,1.12,.86,1.16):
                t=cv2.resize(self.awesome,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
                if t.shape[0]>crop.shape[0] or t.shape[1]>crop.shape[1]:continue
                if float(cv2.matchTemplate(crop,t,cv2.TM_CCOEFF_NORMED).max())>=.77:return 'awesome',(x,y)
        return None,None

    def clock(self):
        if not self.has('pm'):return None
        # Fixed-width clock: hour right-aligned, two minute digits.
        chars=[]
        for a,b in [(1557,1568),(1568,1579),(1588,1599),(1599,1610)]:
            gs=self.glyphs(self.img[898:919,a:b]);
            if not gs: chars.append('');continue
            chars.append(self.digit(max(gs,key=lambda g:g.size)))
        if None in chars:return None
        try:
            h=int(''.join(chars[:2]));m=int(''.join(chars[2:]));return h*60+m if 1<=h<=12 and 0<=m<60 else None
        except ValueError:return None
    def hack_cue(self):
        # Terminal/glitch evidence takes precedence over incidental matches
        # of the EXIT or offline templates. Both colors are distributed over
        # the screen, unlike ordinary slot symbols or the BitHit button.
        b,g,r=cv2.split(self.img[::4,::4]); rr=r.astype('int16');gg=g.astype('int16');bb=b.astype('int16')
        red=(r>115)&(rr>gg*1.8)&(rr>bb*1.5)&(b<100)
        green=(g>110)&(gg>rr*1.8)&(gg>bb*1.5)&(b<100)
        def coverage(mask):
            return sum(tile.mean()>.012 for row in np.array_split(mask,4,axis=0) for tile in np.array_split(row,4,axis=1))
        if red.mean()>.025 and green.mean()>.025 and coverage(red)>=3 and coverage(green)>=3 and float(self.img.mean())<85:
            return 'hack_terminal'
        if self.has('hack_skull') and float(self.img.mean())<50:return 'hack_skull'
        return None
    def danger(self):
        if self._danger_cached:return self._danger_value
        d=self.hack_cue()
        if not d:
            if self.has('offline'):d='offline'
            elif self.has('power') and float(self.img.mean())<20:d='power'
            elif self.has('death'):d='death'
        self._danger_value=d;self._danger_cached=True
        return d
    @staticmethod
    def text_feature(img):
        # Local contrast rather than a fixed white threshold: dialog text can
        # fade or be antialiased differently as the character moves behind it.
        gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
        return cv2.morphologyEx(gray,cv2.MORPH_TOPHAT,cv2.getStructuringElement(cv2.MORPH_RECT,(7,7)))
    def overwrite_details(self):
        if self._overwrite_cache is not None:return self._overwrite_cache
        keys=('overwrite_title','overwrite_text','overwrite_sure','overwrite_yes','overwrite_no')
        if not hasattr(self,'_overwrite_templates'):
            self._overwrite_templates={}
            for k in keys:
                image=self.templates[k];t=self.text_feature(image)
                # Remove colored background highlights from the source template.
                mask=(image.min(axis=2)>100).astype('uint8')
                mask=cv2.dilate(mask,np.ones((3,3),np.uint8))
                self._overwrite_templates[k]=t*mask
        # Search only the known central dialog region, never the whole screen.
        feature=self.text_feature(self.img[350:570,460:1190])
        best={};result=None
        for scale in (1.,.98,1.02,.95,1.05):
            m={}
            for k in keys:
                x,y,X,Y=self.spec[k]['roi'];t=self._overwrite_templates[k]
                if scale!=1.:t=cv2.resize(t,None,fx=scale,fy=scale,interpolation=cv2.INTER_LINEAR)
                h,w=t.shape;c=feature[y-350:Y-350,x-460:X-460]
                if h>c.shape[0] or w>c.shape[1]:m[k]=(0.,(0,0));continue
                _,score,_,pos=cv2.minMaxLoc(cv2.matchTemplate(c,t,cv2.TM_CCOEFF_NORMED))
                # Reject almost black frames even when their normalized score is high.
                patch=c[pos[1]:pos[1]+h,pos[0]:pos[0]+w]
                if np.max(patch)<20:score=0.
                m[k]=(float(score),(x+pos[0]+w//2,y+pos[1]+h//2))
                best[k]=max(best.get(k,0.),float(score))
            if any(m[k][0]<self.spec[k]['threshold'] for k in keys[:3]):continue
            tx,ty=m['overwrite_title'][1];lx,ly=m['overwrite_text'][1];sx,sy=m['overwrite_sure'][1]
            if not (abs(tx-lx)<=20 and abs(tx-sx)<=20 and 23<=ly-ty<=43 and 19<=sy-ly<=35):continue
            # All three distinctive prompt lines must match. One or both
            # buttons must also match the expected position; a highlighted
            # button is allowed to differ in brightness/color.
            ys,yp=m['overwrite_yes'];ns,np_=m['overwrite_no']
            yx,yy=yp;nx,ny=np_
            yes_ok=ys>=self.spec['overwrite_yes']['threshold'] and abs(yx-(tx-70*scale))<=14 and abs(yy-(sy+47*scale))<=10
            no_ok=ns>=self.spec['overwrite_no']['threshold'] and abs(nx-(tx+57*scale))<=14 and abs(ny-(sy+47*scale))<=10
            if not (yes_ok or no_ok):continue
            result=yp if yes_ok else (round(tx-70*scale),round(sy+47*scale))
            break
        self._overwrite_cache=(result,best)
        return self._overwrite_cache
    def overwrite_yes(self):
        return self.overwrite_details()[0]
    def overwrite_debug(self):
        return ' '.join(f'{k.removeprefix("overwrite_")}={v:.2f}' for k,v in self.overwrite_details()[1].items())
    def state(self):
        if self.overwrite_yes() is not None:return 'overwrite'
        for k in ['menu','difficulty','pause','story','skip','spawn']:
            if self.has(k):return k
        d=self.danger()
        if d:return d
        if self.has('spawn_door'):return 'spawn'
        if self.has('merr_title'):
            if self.loss_confirmed():return 'loss'
            payout,_=self.payout_info()
            if payout:return payout
            for k in ['processing','better']:
                if self.has(k):return k
            return self.spin_state() or 'merr_unknown'
        if self.on_desktop():return 'desktop'
        if self.desk_prompt():return self.desk_prompt()
        return 'unknown'
