from pathlib import Path
import sys, json, time, logging, datetime, traceback
import cv2
import numpy as np
import base64
from vision import Vision, ROOT, load_image
from stake import StakeControls
from achievement_watch import AchievementWatch, Evidence, JuicedUnlocked, write_bytes

if len(sys.argv)>1 and sys.argv[1]=='--analyze':
    v=Vision().set(load_image(sys.argv[2]));print('state:',v.state(),'clock:',v.clock(),'DOS/YoloYen:',v.balances(),'Continue:',v.match('continue_save')[0],'stake:',StakeControls().inspect(v));sys.exit()
if sys.platform!='win32':sys.exit('This bot controls a Windows game. Use --analyze IMAGE for offline analysis.')
from winio import Window, Recorder, Halt, foreground, title, pressed
import winsound
from fastclick import FastClicker
from spin_counter import SpinStats, SpinObserver
CFG=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
LOG=ROOT/'logs';LOG.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',handlers=[logging.FileHandler(LOG/'bot.log',encoding='utf-8'),logging.StreamHandler(sys.stdout)])
log=logging.info
ROUTE=ROOT/'route.json'
RESPAWN_ROUTE=ROOT/'respawn_route.json'
MAX_ROUTE_ATTEMPTS=3
DARK_HOLD_SECONDS=4.0
STALL_SECONDS=60.0
STAKE=100.0
RESPAWN_WAIT_SECONDS=20.0
SPAWN_MATCH_THRESHOLD=.90
HACK_SKIP_SECONDS=90.0
HACK_POWER_CONFIRM_SECONDS=6.0
HACK_CLICK_INTERVAL=.25
PAYOUT_GRACE_SECONDS=20.0
HACKS=('hack_skull','hack_terminal')

class RouteRetry(Exception):
    """Recoverable failed new-run navigation; not a manual/safety halt."""

class ProgressWatchdog:
    """Elapsed time since the last newly counted spin (not animation clicks)."""
    def __init__(self, total, now, timeout=STALL_SECONDS):
        self.timeout=timeout
        self.reset(total,now)

    def reset(self,total,now,mode='spins'):
        self.mode=mode
        self.total=total
        self.last_progress=now

    def expired(self,total,now,mode='spins'):
        # When the counter itself is disabled, fall back to click activity
        # rather than incorrectly claiming that every real spin is stuck.
        if mode!=self.mode or total>self.total:
            self.reset(total,now,mode)
        return now-self.last_progress>=self.timeout


def dark_frame(v):
    # A nearly black frame may be a normal fade. Only act on persistence,
    # and only while recovering/after an established game desktop.
    g=cv2.cvtColor(v.img[::4,::4],cv2.COLOR_BGR2GRAY)
    return float(g.mean())<8 and float((g<18).mean())>.95

def dark_respawn(v):
    # Additional view supplied by the user: a flashlight-lit skeleton,
    # shifted relative to the normal respawn. Never replay a route from
    # this image alone; it is evidence to prefer a fresh game.
    return 'dark_respawn' in v.templates and v.has('dark_respawn')

def anchor_view(v):
    # Exclude HUD/tutorial and borders; retain the room geometry.
    gray=cv2.cvtColor(v.img,cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray[110:870,50:1600],(160,80),interpolation=cv2.INTER_AREA)

def encode_anchor(v):
    return base64.b64encode(anchor_view(v).tobytes()).decode('ascii')

def anchor_score(v,encoded):
    try:
        a=np.frombuffer(base64.b64decode(encoded,validate=True),np.uint8).reshape(80,160)
    except (ValueError,TypeError):
        return 0.0
    b=anchor_view(v)
    if min(a.std(),b.std())<8 or b.mean()<10:return 0.0
    return float(cv2.matchTemplate(b,a,cv2.TM_CCOEFF_NORMED)[0,0])

def read_recorded_route(path):
    if not path.exists():return None
    try:
        obj=json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(obj['events'],list) or len(obj['events'])<2
                or not isinstance(obj['anchor'],str)):raise ValueError('missing route or anchor')
        if len(base64.b64decode(obj['anchor'],validate=True))!=160*80:raise ValueError('invalid anchor size')
        return obj
    except (OSError,ValueError,KeyError,TypeError) as e:
        log('Route %s ignored: %s',path.name,e)
        return None


def signal(kind):
    # Play only after the recording action has actually succeeded or failed.
    tones={'start':[(1250,180)],'saved':[(1250,140),(1600,180)],'error':[(350,350)]}
    try:
        for i,(freq,duration) in enumerate(tones[kind]):
            if i:time.sleep(.09)
            winsound.Beep(freq,duration)
    except Exception:
        log('Audio notification unavailable: %s',kind)


class Bot:
    def __init__(self,w):self.w=w;self.v=Vision();self.stake=StakeControls();self.spin_stats=SpinStats();self.loss_count=0;self.restarts=0;self.last_img=None
    def frame(self):
        self.last_img=self.w.capture()
        v=self.v.set(self.last_img)
        evidence=getattr(self,'juiced_evidence',None)
        watch=getattr(self,'juiced_watch',None)
        if evidence is not None:
            now=time.monotonic()
            # Check the local cache BEFORE any further gameplay decisions.
            hit=watch is not None and watch.poll(now) is True
            if hit and hasattr(self,'clicker'):self.clicker.pause()
            evidence.record(self.last_img,now)
            evidence.observe_balance(v,self.last_img,now)
            if hit:
                self.juiced_timestamp=watch.timestamp
                raise JuicedUnlocked()
        return v
    def snapshot(self,reason):
        if self.last_img is not None:
            fn=LOG/(datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.png');cv2.imencode('.png',self.last_img)[1].tofile(str(fn));log('Screenshot: %s (%s)',fn.name,reason)
    def stop(self,reason):
        if hasattr(self,'clicker'):self.clicker.pause()
        self.snapshot(reason);raise Halt(reason)
    def until(self,test,timeout,reason):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            v=self.frame()
            if test(v):return v
            self.w.wait(.25)
        self.stop(reason)
    def open_app(self,icon,header):
        v=self.frame()
        if v.has(header):return
        if not v.has(icon):self.stop('Cannot find '+icon)
        p=v.pos(icon);self.w.click(*p)
        end=time.monotonic()+3
        while time.monotonic()<end:
            v=self.frame()
            if v.has(header):return
            self.w.wait(.2)
        v=self.frame()
        if not v.has(icon):self.stop('Icon disappeared: '+icon)
        p=v.pos(icon);self.w.click(*p);self.w.click(*p)
        self.until(lambda a:a.has(header),5,'Cannot open '+header)
    def skip_hack(self,route=None,suspected=False):
        # Latch the event across the black frames and all minigame phases.
        # Middle clicks run independently of screen analysis; no LMB or ESC.
        if hasattr(self,'clicker'):self.clicker.pause()
        route=route or getattr(self,'save_route',None)
        log('Possible hack / dark transition; pressing middle mouse' if suspected else 'Hack detected; middle mouse skip mode (including black frames)')
        self.snapshot('Suspected hack / black transition' if suspected else 'Hack detected before middle-button skip')
        end=time.monotonic()+(STALL_SECONDS if suspected else HACK_SKIP_SECONDS)
        clear_since=None;last_report=0;hazard_since=None;last_hazard=None
        saw_hack=not suspected
        def middle_pulse(_x,_y):
            self.w.middle_click(hold=.12)
        pulses=FastClicker(middle_pulse)
        try:
            pulses.enable(0,0,HACK_CLICK_INTERVAL,lease=2.0)
            while time.monotonic()<end:
                pulses.check()
                v=self.frame();d=v.danger();now=time.monotonic()
                if d in HACKS:
                    if not saw_hack:
                        saw_hack=True;end=max(end,now+HACK_SKIP_SECONDS)
                        log('Hack confirmed during dark transition; continuing middle clicks')
                    clear_since=None;hazard_since=None;last_hazard=None
                    pulses.enable(0,0,HACK_CLICK_INTERVAL,lease=2.0)
                else:
                    black=dark_frame(v)
                    score,_=self.spawn_match(v,route)
                    hazard=d if d in ('offline','power') else 'dark respawn' if dark_respawn(v) and score<.90 else None
                    menu_hazard=None
                    # Never click in a recognized menu. Do not use a lone
                    # black frame or a brief visual artifact to infer power loss.
                    if v.has('menu'):menu_hazard='Main menu during hack'
                    elif v.has('pause'):menu_hazard='Pause menu during hack'
                    elif v.has('difficulty') or v.overwrite_yes() is not None:menu_hazard='Unexpected new-game screen during hack'
                    if menu_hazard:hazard=menu_hazard
                    if hazard:
                        clear_since=None
                        if hazard!=last_hazard:hazard_since=now;last_hazard=hazard
                        if menu_hazard:pulses.pause()
                        else:pulses.enable(0,0,HACK_CLICK_INTERVAL,lease=2.0)
                        confirm=1.0 if menu_hazard else HACK_POWER_CONFIRM_SECONDS
                        if now-hazard_since>=confirm:
                            log('Confirmed persistent hazard after hack: %s',hazard)
                            return hazard
                    else:
                        hazard_since=None;last_hazard=None
                        desktop=(not black and v.on_desktop() and
                                 (v.has('desktop') or v.slot_frame() or
                                  (v.has('bar_yolo') and v.has('bar_dos'))))
                        known_room=not black and (v.desk_prompt() or v.has('spawn_door') or d=='death' or score>=.90)
                        if desktop or known_room:
                            pulses.pause()
                            if clear_since is None:clear_since=now
                            if now-clear_since>=.8:
                                log('Hack ended | middle clicks=%s | desktop=%s | room=%s | anchor=%.3f',pulses.count,desktop,bool(known_room),score)
                                return None
                        else:
                            clear_since=None
                            # Includes completely black transition frames.
                            pulses.enable(0,0,HACK_CLICK_INTERVAL,lease=2.0)
                if now>=last_report:
                    log('Hack skip active | detected=%s | middle clicks=%s | remaining=%.0fs',d,pulses.count,max(0,end-now))
                    last_report=now+3
                self.w.wait(.08)
            self.snapshot('Hack did not return to a verified playable screen before timeout')
            return 'Hack skip timed out; reload existing save'
        finally:
            count=pulses.close()
            log('Middle mouse skip stopped | clicks=%s',count)

    def finish_hack_to_desktop(self,suspected=False):
        # A skip can return to the wall, monitor, or already-open Merramun.
        reason=self.skip_hack(getattr(self,'save_route',None),suspected=suspected)
        if reason:return reason
        v=self.frame()
        if not v.danger() and v.on_desktop():return None
        if not getattr(self,'full_mode',False):
            return 'Hack ended outside desktop; F9 can recover using the route'
        log('Hack ended outside desktop; checking desk prompt and recorded routes before considering reload')
        try:
            self.wait_loaded_save(getattr(self,'save_route',None))
        except RouteRetry as exc:
            return str(exc)
        return None

    def ensure_stake(self):
        if hasattr(self,'clicker'):self.clicker.pause()
        deadline=time.monotonic()+30;clicks=0;last_report=0;stable=0
        last_anchor=None;payout_until=0.0
        while time.monotonic()<deadline and clicks<240:
            v=self.frame();d=v.danger()
            if d in HACKS:
                reason=self.finish_hack_to_desktop()
                if reason:return reason
                v=self.frame()
                if not v.has('merr_title') and v.has('merr_icon'):
                    self.w.click(*v.pos('merr_icon'))
                deadline=time.monotonic()+30;stable=0
                continue
            if v.has('merr_title'):last_anchor=v.match('merr_title')[1][:2]
            kind,anchor=v.payout_info(last_anchor)
            now=time.monotonic()
            if kind:
                if now>=payout_until:log('Payout before stake setup | %s; accelerating before setting stake',kind)
                payout_until=now+PAYOUT_GRACE_SECONDS
                deadline=max(deadline,payout_until+30)
                x,y,offset=self.animation_target(v,anchor)
                self.w.click_fast(x+offset,y);self.w.wait(.03);continue
            if d:return d
            if v.has('menu'):return 'main menu during stake selection'
            if v.has('merr_title') and v.loss_confirmed():return self.max_loss_reason()
            if now<payout_until and v.spin_state()!='spin':
                if v.has('merr_title'):
                    x,y,offset=self.animation_target(v)
                    self.w.click_fast(x+offset,y)
                self.w.wait(.08);continue
            state,pos,detail=self.stake.inspect(v)
            if state=='max' and not v.loss_confirmed():
                stable+=1
                if stable>=2:
                    log('Stake confirmed 100.00 | plus clicks=%s',clicks);return None
                self.w.wait(.12);continue
            stable=0
            if state=='raise' and not v.loss_confirmed() and not v.has('processing'):
                # Click in quick bursts; the game caps the value at 100.
                # Re-identify the actual plus before every burst.
                self.w.rapid_click(*pos,8,.06);clicks+=8
                self.w.move(pos[0]+42,pos[1]-25)
            elif state=='limited':
                _,yolo=v.taskbar_balances()
                if yolo is not None and yolo<STAKE:
                    return 'Balance below 100 while setting stake; reload save'
                self.w.wait(.2)
            else:self.w.wait(.2)
            if time.monotonic()>=last_report:
                log('Setting stake 100 | state=%s | %s | plus clicks=%s',state,detail,clicks);last_report=time.monotonic()+2
        return 'Stake 100 not confirmed; reload save (no spins at an unverified stake)'

    def setup(self):
        v=self.frame()
        if v.danger() in HACKS:
            reason=self.finish_hack_to_desktop()
            if reason:return reason
            v=self.frame()
        if v.danger():return v.danger()
        # A skipped hack may return to the seated monitor rather than the
        # full-screen desktop. Enter only when the visible prompt confirms it.
        if not v.on_desktop() and v.desk_prompt():
            deadline=time.monotonic()+8;next_click=0
            while time.monotonic()<deadline:
                v=self.frame();d=v.danger()
                if d in HACKS:
                    reason=self.finish_hack_to_desktop()
                    if reason:return reason
                    deadline=time.monotonic()+8
                    continue
                if d:return d
                if v.on_desktop():break
                if v.desk_prompt() and time.monotonic()>=next_click:
                    self.w.click(825,463);next_click=time.monotonic()+1.5
                self.w.wait(.25)
        if not v.on_desktop():return 'Game desktop not detected'
        # A startup hack can appear as the slot app opens; retry once after skip.
        if not v.has('merr_title'):
            if not v.has('merr_icon'):return 'Merramun icon not visible'
            self.w.click(*v.pos('merr_icon'))
            end=time.monotonic()+12;next_open=time.monotonic()+2
            while time.monotonic()<end:
                v=self.frame();d=v.danger()
                if d in HACKS:
                    reason=self.finish_hack_to_desktop()
                    if reason:return reason
                    end=time.monotonic()+12;next_open=time.monotonic()
                    continue
                if d:return d
                if v.has('merr_title'):break
                if v.has('merr_icon') and time.monotonic()>=next_open:
                    self.w.click(*v.pos('merr_icon'));self.w.click(*v.pos('merr_icon'));next_open=time.monotonic()+2
                self.w.wait(.2)
            else:return 'Merramun not visible after opening'
        reason=self.ensure_stake()
        if reason:return reason
        log('Continuous SAVE100 farm ready. Stake 100.00. BitHit disabled. Recovery loads Continue only; F10 to stop.')
        return None
    def max_loss_reason(self):
        if hasattr(self,'clicker'):self.clicker.pause()
        self.loss_count+=1
        log('MAX LOSS #%s; reload existing save (BitHit disabled)',self.loss_count)
        return 'MAX LOSS; reload save (no exchange)'

    def animation_target(self,v,anchor=None):
        # The spin button occupies the same lower-center position as ordinary
        # result text. Clicking here skips normal animations without touching
        # the stake buttons, window titlebar, or the exchange app.
        mx,my=anchor if anchor is not None else v.match('merr_title')[1][:2]
        x,y=mx+322,my+666
        if not (0<=x<1650 and 0<=y<927):self.stop('Merramun click area outside game window')
        # Select a separate animation click point in the lower slot panel,
        # away from spin, stake controls and title. Check it for overlap too.
        offset=110
        if v.has('bit_title'):
            bx,by,_,_=v.match('bit_title')[1]
            if by-8<=y<=by+412:
                if bx-8<=x<=bx+380:
                    self.stop('BitHit overlaps Merramun click area; move BitHit to the left')
                if bx-8<=x+offset<=bx+380:
                    offset=-110
                if bx-8<=x+offset<=bx+380:
                    self.stop('BitHit overlaps animation click area; move BitHit to the left')
        if not 0<=x+offset<1650:self.stop('Animation click area outside game window')
        return x,y,offset

    def rapid_click(self,v,anchor=None):
        x,y,offset=self.animation_target(v,anchor)
        self.spin_observer.animation_offset=offset
        interval=max(.025,min(.5,float(CFG.get('continuous_click_interval_seconds',.05))))
        count=self.clicker.enable(x,y,interval,lease=2.0)
        if self.fast_click_count is None or count//100>self.fast_click_count//100:
            log('Continuous clicker active. SpinsTotal=%s SpinsSession=%s | Clicks=%s (not spins) MAX LOSS=%s',self.spin_stats.total,self.spin_observer.tracker.session,count,self.loss_count)
            self.fast_click_count=count
    def farm(self,started):
        reason=self.setup()
        if reason:return reason
        self.spin_observer=SpinObserver(self.w,self.v,self.spin_stats)
        self.clicker=FastClicker(self.spin_observer.click, on_close=self.spin_observer.close)
        self.fast_click_count=None
        try:
            return self.farm_loop(started)
        finally:
            count=self.clicker.close()
            log('Continuous clicker stopped. SpinsTotal=%s SpinsSession=%s | Clicks=%s (not spins)',self.spin_stats.total,self.spin_observer.tracker.session,count)
    def farm_loop(self,started):
        grey_since=None
        last_state=None
        last_positive=None;last_log=0;black_since=None;power_since=None
        last_anchor=None;payout_until=0.0;payout_active=False;last_payout_report=0;last_panel=None
        watchdog=ProgressWatchdog(self.spin_stats.total,time.monotonic())
        log('Progress watchdog active: %ss without a counted spin; stake setup/loading excluded',int(STALL_SECONDS))
        while True:
            self.clicker.check();v=self.frame();now=time.monotonic();d=v.danger()
            mode='spins' if self.spin_stats.enabled and self.spin_observer.enabled else 'clicks'
            progress=self.spin_stats.total if mode=='spins' else self.clicker.count
            title_ok=v.has('merr_title')
            if title_ok:last_anchor=v.match('merr_title')[1][:2]
            kind,anchor=v.payout_info(last_anchor)
            # Check the payout BEFORE watchdog, low balance, and generic
            # transition handling. Never reload a visibly unresolved award.
            if kind and d not in HACKS:
                if not payout_active or now>=last_payout_report:
                    log('Payout protected | %s | clicking to finish; watchdog suspended',kind)
                    last_payout_report=now+5
                payout_active=True;payout_until=now+PAYOUT_GRACE_SECONDS
                watchdog.reset(progress,now,mode);grey_since=None;black_since=None;power_since=None
                self.rapid_click(v,anchor);self.w.wait(.02);continue
            if payout_active:
                # Real power/network/hack/death can interrupt an award.
                # A black fade alone is NOT an emergency in this grace period.
                actual_hazard=(d in HACKS or d=='offline' or
                               (d in ('power','death') and not title_ok) or v.has('menu') or v.has('pause'))
                ready=title_ok and v.spin_state()=='spin'
                loss=title_ok and v.loss_confirmed()
                if ready or loss or actual_hazard or now>=payout_until:
                    log('Payout protection released | ready=%s loss=%s hazard=%s grace_expired=%s',ready,loss,actual_hazard,now>=payout_until)
                    payout_active=False;watchdog.reset(progress,now,mode)
                else:
                    watchdog.reset(progress,now,mode);grey_since=None;black_since=None;power_since=None
                    if title_ok and not d:self.rapid_click(v)
                    else:self.clicker.pause()
                    self.w.wait(.05);continue
            # For an unfamiliar moving overlay, also recognize visual
            # progress (not counted as spins). Static unknown frames retain
            # the normal watchdog limit. Exclude confirmed loss and hazards.
            if title_ok and not d and not v.loss_confirmed() and v.spin_state() is None:
                x,y=last_anchor
                roi=v.img[y+100:min(y+780,927),x+30:min(x+610,1650)]
                if roi.size:
                    panel=cv2.resize(cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY),(64,60),interpolation=cv2.INTER_AREA)
                    if last_panel is not None and float(np.abs(panel.astype('float32')-last_panel.astype('float32')).mean())>2.5:
                        watchdog.reset(progress,now,mode)
                    last_panel=panel
            else:last_panel=None
            # A pure black transition is not proof that the lights went
            # out. Even if the colored intro was missed between captures,
            # try the middle-button skip before reloading the save.
            if d=='power':
                self.clicker.pause();black_since=None
                if power_since is None:power_since=now
                if now-power_since>=2.0:return 'power'
                self.w.wait(.10);continue
            power_since=None
            if d in HACKS: suspected=False;recover_hack=True;black_since=None
            elif not d and dark_frame(v):
                self.clicker.pause()
                if black_since is None:black_since=now
                suspected=True;recover_hack=now-black_since>=.8
                if not recover_hack:self.w.wait(.10);continue
            else:suspected=False;recover_hack=False;black_since=None
            if recover_hack:
                reason=self.finish_hack_to_desktop(suspected=suspected)
                if reason:return reason
                reason=self.setup()
                if reason:return reason
                watchdog.reset(self.spin_stats.total,time.monotonic())
                last_state=None;grey_since=None;black_since=None;power_since=None;last_anchor=None;last_panel=None;payout_active=False
                continue
            if d:return d
            if v.has('menu'):return 'main menu'
            if v.has('pause'):self.stop('Game paused unexpectedly')
            # A natural respawn may not match the skeleton/death template.
            # Confirm the recorded room instead of waiting for the watchdog.
            if getattr(self,'full_mode',False) and not title_ok and not v.on_desktop():
                score,matched=self.spawn_match(v,getattr(self,'respawn_route',None))
                if matched and score>=SPAWN_MATCH_THRESHOLD and not dark_frame(v):
                    self.clicker.pause()
                    log('Recorded respawn view detected during farm | anchor %.3f',score)
                    return 'death'
            mode='spins' if self.spin_stats.enabled and self.spin_observer.enabled else 'clicks'
            progress=self.spin_stats.total if mode=='spins' else self.clicker.count
            if mode!=watchdog.mode:
                log('Spin counter unavailable; watchdog falls back to click activity (not spin accuracy)')
            if watchdog.expired(progress,now,mode):
                self.clicker.pause()
                return 'Watchdog: no new '+mode+' for 60 seconds; recover via menu'
            td,ty=v.taskbar_balances();bd,by=v.bithit_balances()
            # Use a reliable positive reading to continue clicking.
            # Never open or submit BitHit in SAVE100.
            yolo=ty if ty is not None else by;dos=td if td is not None else bd
            if ty is not None and by is not None and abs(ty-by)>.01:
                yolo=max(ty,by) if max(ty,by)>=STAKE else None
            if yolo is not None and yolo>=STAKE:last_positive=now
            if not v.has('merr_title'):
                self.clicker.pause();grey_since=None
                if dark_respawn(v):
                    log('Dark/shifted respawn view detected; check recovery before menu')
                    return 'dark respawn'
                # No fast restart based only on a black or unfamiliar frame.
                # The watchdog covers a stalled slot even if the clicker is
                # still clicking, as well as a lost title or long result.
                self.w.wait(.15);continue
            if v.loss_confirmed():return self.max_loss_reason()
            if v.has('processing'):state='processing'
            elif v.has('tumble'):state='tumble'
            elif v.has('better'):state='better'
            else:state=v.spin_state() or 'transition'
            if state!=last_state or now-last_log>=5:
                log('State=%s | DOS=%s YoloYen=%s | taskbar=%s | BitHit=%s',state,dos,yolo,(td,ty),(bd,by));last_log=now
            last_state=state
            if state=='processing':
                self.clicker.pause();grey_since=None
                self.w.wait(.15);continue
            if state in ('spin','disabled'):
                stake_state,_,_=self.stake.inspect(v)
                if stake_state=='raise' or (state=='spin' and stake_state!='max'):
                    self.clicker.pause()
                    reason=self.ensure_stake()
                    if reason:return reason
                    watchdog.reset(self.spin_stats.total,time.monotonic())
                    grey_since=None;continue
                # After setup, a visibly active spin at a verified stake
                # can establish playability even if a large balance is unreadable.
                if state=='spin' and stake_state=='max':last_positive=now
            if state=='disabled' and ty is not None and ty<STAKE and (by is None or by<STAKE):
                self.clicker.pause()
                if grey_since is None:grey_since=now
                # Wait for a stable inactive button AND confirmed lack of YoloYen.
                if now-grey_since>=5:
                    log('YoloYen depleted: %.2f; reload existing save (BitHit disabled)',ty)
                    return 'YoloYen below 100; reload save (no exchange)'
                self.w.wait(.15);continue
            grey_since=None
            if state=='transition':
                # Large wins and jackpot-like overlays can hide the reel frame.
                # With a verified Merramun title and desktop/slot evidence, the
                # same bounded click target can dismiss them, without interpreting
                # a large balance or an unknown animation as an achievement.
                # A missing title is handled separately above; never click blind.
                if v.slot_frame() or v.has('desktop') or (td is not None and ty is not None):
                    self.rapid_click(v);self.w.wait(.02);continue
                self.clicker.pause()
                self.w.wait(.10);continue
            # A grey button can also appear momentarily during normal spinning.
            # Never restart on grey alone. Keep accelerating normal
            # result animations even if the stake temporarily depleted the balance.
            allowed=(state in ('tumble','better') or
                     (state=='spin' and stake_state=='max') or
                     (yolo is not None and yolo>=STAKE) or
                     (yolo is None and last_positive is not None and now-last_positive<3))
            if allowed:
                self.rapid_click(v)
                self.w.wait(.02);continue
            self.clicker.pause()
            self.w.wait(.12)
    def wait_menu(self,timeout=25):
        # The menu can be briefly recognized then obscured by its glitch
        # animation. Never assume the next captured frame is also ready.
        end=time.monotonic()+timeout;stable_since=None;last_pos=None
        while time.monotonic()<end:
            v=self.frame()
            if v.has('menu'):
                pos=v.pos('menu')
                if stable_since is None or last_pos is None or max(abs(pos[i]-last_pos[i]) for i in (0,1))>6:
                    stable_since=time.monotonic()
                last_pos=pos
                if time.monotonic()-stable_since>=.65:return v
            else:
                stable_since=None;last_pos=None
            self.w.wait(.25)
        self.stop('Main menu not stable after waiting; inspect screenshot')

    def return_menu(self):
        log('Returning to main menu')
        end=time.monotonic()+25;next_esc=0
        while time.monotonic()<end:
            v=self.frame()
            if v.has('menu'):
                self.wait_menu();return
            if v.has('pause') and v.has('to_menu'):
                self.w.click(*v.pos('to_menu'))
                log('Waiting for main menu animation to finish')
                self.wait_menu(30);return
            if time.monotonic()>=next_esc:
                self.w.tap(0x01);next_esc=time.monotonic()+2
            self.w.wait(.35)
        self.stop('Cannot reach pause/main menu. Inspect hack/death screen; automatic process kill is disabled.')

    def spawn_match(self,v,preferred=None):
        # Use the same position check for Continue and natural respawns.
        # Prefer the dedicated F4 route on equal scores during death recovery.
        routes=(preferred,getattr(self,'respawn_route',None),getattr(self,'save_route',None))
        return max(((anchor_score(v,r['anchor']),r) for r in routes if r),
                   key=lambda item:item[0],default=(0.0,None))

    def resume_after_death(self,respawn):
        if hasattr(self,'clicker'):self.clicker.pause()
        log('Respawn recovery: checking both recorded spawn anchors before menu')
        try:
            self.wait_loaded_save(respawn,after_death=True)
        except RouteRetry as exc:
            log('Respawn recovery could not verify/reach desktop: %s; reloading save',exc)
            self.snapshot('Respawn recovery failed: '+str(exc))
            return False
        log('Respawn recovery reached desktop; resuming farm without menu')
        return True

    def continue_save(self,route):
        v=self.wait_menu()
        if not v.has('continue_save'):self.stop('Continue button not recognized; no new game will be started')
        self.w.click(*v.pos('continue_save'));log('Clicked Continue (existing save only)')
        return self.wait_loaded_save(route,allow_menu_retry=True)

    def wait_loaded_save(self,route=None,allow_menu_retry=False,after_death=False):
        end=time.monotonic()+(RESPAWN_WAIT_SECONDS if after_death else 100);last_report=0;stable_since=None;retried=False;retry_after=time.monotonic()+4
        next_middle=time.monotonic()+2;probes=0;next_desk=0;desk_attempts=0;hack_count=0;stable_route=None;dark_since=None
        routes=[r for r in (route,getattr(self,'respawn_route',None),getattr(self,'save_route',None)) if r]
        best_score=0.0
        while time.monotonic()<end:
            v=self.frame();d=v.danger();now=time.monotonic()
            if d in HACKS:
                hack_count+=1
                if hack_count>3:raise RouteRetry('Repeated hacks prevented loading the save')
                reason=self.skip_hack(route);stable_since=None;stable_route=None;dark_since=None
                if reason:raise RouteRetry(reason)
                # Give navigation its own time budget after the hack.
                end=max(end,time.monotonic()+(RESPAWN_WAIT_SECONDS if after_death else 60))
                next_middle=time.monotonic()+2;probes=0
                continue
            if d in ('offline','power'):raise RouteRetry(d)
            score,matched=self.spawn_match(v,route)
            best_score=max(best_score,score)
            if dark_respawn(v) and score<SPAWN_MATCH_THRESHOLD:
                stable_since=None;stable_route=None
                if dark_since is None:dark_since=now
                if now-dark_since>=DARK_HOLD_SECONDS:raise RouteRetry('Dark respawn after loading save')
                self.w.wait(.25);continue
            dark_since=None
            if v.on_desktop():
                log('Loaded save is already on game desktop; no route needed')
                return time.monotonic()
            if v.overwrite_yes() is not None or v.has('difficulty'):
                self.stop('New-game/overwrite screen detected; SAVE100 refuses to overwrite a save')
            if v.has('pause'):raise RouteRetry('Pause menu during load')
            if v.has('menu'):
                if after_death:raise RouteRetry('Death returned to main menu')
                if allow_menu_retry and not retried and now>=retry_after and v.has('continue_save'):
                    v=self.wait_menu(8)
                    if not v.has('continue_save'):self.stop('Continue disappeared; no new game will be started')
                    self.w.click(*v.pos('continue_save'));retried=True;log('Retried Continue')
                stable_since=None
            else:
                prompt=v.desk_prompt()
                if prompt:
                    stable_since=None
                    if now>=next_desk and desk_attempts<6:
                        log('Recognized %s; clicking center to enter desk',prompt)
                        self.w.click(825,463);desk_attempts+=1;next_desk=now+1.5
                    self.w.wait(.25);continue
                if matched and score>=SPAWN_MATCH_THRESHOLD and not dark_frame(v):
                    if stable_since is None or stable_route is not matched:stable_since=now;stable_route=matched
                    if now-stable_since>=1.0:
                        log('%s spawn confirmed | anchor %.3f | events=%s', 'Respawn' if after_death else 'Save/respawn',score,len(matched['events']))
                        return self.follow_save_route(matched)
                else:
                    stable_since=None
                    # After Continue, or explicit F9 from an unknown game
                    # view, the save sometimes begins inside an unrecognized
                    # hack. A few middle-button presses only; never blind LMB.
                    if (not after_death and probes<12 and now>=next_middle and not dark_frame(v) and
                            not d and not v.has('spawn_door') and not v.has('spawn')):
                        self.w.middle_click(hold=.12);probes+=1;next_middle=time.monotonic()+1.0
                        log('Startup middle-button probe %s/12 (possible unrecognized hack)',probes)
                if now>=last_report:
                    log('Waiting for %s desktop/spawn | route=%s | anchor %.3f | best %.3f | danger=%s','respawn' if after_death else 'save',bool(routes),score,best_score,d);last_report=now+5
            self.w.wait(.25)
        if after_death:raise RouteRetry('Respawn position not confirmed (best anchor %.3f; need %.2f)'%(best_score,SPAWN_MATCH_THRESHOLD))
        if not routes:raise RouteRetry('No desktop or desk prompt after loading; if standing, record F7 after hack and F6 on desktop')
        raise RouteRetry('Saved spawn not detected; check F7 recording after hack')

    def follow_save_route(self,route):
        log('Replaying save route (%s events)',len(route['events']))
        self.w.replay(route['events'])
        deadline=time.monotonic()+STALL_SECONDS;next_desk=0;replayed=False
        while time.monotonic()<deadline:
            v=self.frame();d=v.danger()
            if d in HACKS:
                reason=self.skip_hack(route)
                if reason:raise RouteRetry(reason)
                deadline=max(deadline,time.monotonic()+STALL_SECONDS)
                v=self.frame()
                if not replayed and v.danger() not in HACKS and not v.on_desktop() and not dark_frame(v) and anchor_score(v,route['anchor'])>=.90:
                    log('Hack returned to recorded spawn during route; replaying once')
                    self.w.replay(route['events']);replayed=True
                continue
            if v.on_desktop():
                log('Save route reached desktop');return time.monotonic()
            if d in ('offline','power') or dark_respawn(v):raise RouteRetry(d or 'dark respawn')
            if v.has('menu'):raise RouteRetry('Menu during save route')
            if v.desk_prompt() and time.monotonic()>=next_desk:
                self.w.click(825,463);next_desk=time.monotonic()+1.5
            self.w.wait(.25)
        raise RouteRetry('Save route did not reach desktop in 60s')

    def load_with_recovery(self,route,from_spawn=False):
        for attempt in range(1,MAX_ROUTE_ATTEMPTS+1):
            try:
                if attempt==1 and from_spawn:return self.wait_loaded_save(route)
                return self.continue_save(route)
            except RouteRetry as exc:
                self.snapshot(str(exc));log('Save-route recovery %s/%s: %s',attempt,MAX_ROUTE_ATTEMPTS,exc)
                if attempt>=MAX_ROUTE_ATTEMPTS:self.stop('Save-route recovery exhausted; re-record F7 after loading and skipping hack')
                self.return_menu();self.restarts+=1
        raise AssertionError('unreachable')

    def run(self,full):
        self.juiced_watch=AchievementWatch(ROOT)
        self.juiced_evidence=Evidence(ROOT/'jackpot_evidence')
        self.juiced_timestamp=None
        try:
            initial=self.juiced_watch.poll(force=True)
            if initial is True:
                raise Halt('Juiced is already unlocked in the local Steam cache; no farm started')
            log('Juiced guard active | Steam=%s | rolling evidence up to 120 seconds | F10 remains manual stop', 'locked' if initial is False else 'unknown / retrying')
            return self.run_farm(full)
        except JuicedUnlocked:
            # Farm worker and hack worker are closed by their own finally
            # blocks as the exception unwinds. Never take the reload path.
            self.juiced_watch=None
            self.finish_juiced()
        finally:
            self.juiced_watch=None
            self.juiced_evidence=None

    def finish_juiced(self):
        evidence=self.juiced_evidence
        folder=None;report=None
        log('JUICED UNLOCKED | no more spins, no reload | saving evidence')
        if hasattr(self,'clicker'):self.clicker.pause()
        try:
            folder,report=evidence.save_unlock(self.last_img,self.juiced_timestamp,self.spin_stats.total)
            log('Juiced evidence folder: %s',folder.name)
        except Exception:
            logging.exception('Juiced evidence write failed; farm remains stopped')
        completed=False;paused=False;status='Waiting for safe payout completion'
        last_anchor=None;ready_since=None;next_animation=0.
        try:
            # No fast-click worker remains. Click only a confirmed award
            # animation, away from the spin/stake buttons. Never reload.
            deadline=time.monotonic()+120.
            while time.monotonic()<deadline:
                v=self.frame();now=time.monotonic()
                if v.has('pause'):
                    paused=True;status='Already paused; payout state not verified';break
                if v.has('menu'):
                    status='Already at main menu (Steam cache may have updated late)';break
                title_ok=v.has('merr_title')
                if title_ok:last_anchor=v.match('merr_title')[1][:2]
                kind,anchor=v.payout_info(last_anchor)
                d=v.danger()
                if d:
                    status='Hazard after achievement; no reload or further inputs: '+str(d)
                    break
                if kind:
                    ready_since=None
                    if now>=next_animation:
                        x,y,offset=self.animation_target(v,anchor)
                        # Recheck after computing target; no new spin button click.
                        self.w.click_fast(x+offset,y)
                        next_animation=now+.25
                else:
                    ready=(title_ok and (v.spin_state()=='spin' or v.loss_confirmed()))
                    safe_non_slot=(not title_ok and (v.has('desktop') or bool(v.desk_prompt())))
                    if ready or safe_non_slot:
                        if ready_since is None:ready_since=now
                        if now-ready_since>=1.5:
                            completed=bool(ready)
                            status='Payout screen resolved' if ready else 'No active Merramun screen; safe pause attempted'
                            if folder is not None:
                                try:evidence.save_result(folder,self.last_img,'after_payout_before_pause')
                                except Exception:logging.exception('Could not save final payout image')
                            break
                    else:
                        ready_since=None
                self.w.wait(.15)
            else:
                status='Payout could not be verified within 120 seconds; stopped without pressing Esc'
            # Do not pause in the middle of an unverified award, nor press
            # Esc from the main menu. Confirm pause with vision, never click
            # the to-menu button. A manual F10/focus loss still takes priority.
            if not paused and (completed or status.startswith('No active Merramun')):
                for _ in range(3):
                    v=self.frame()
                    if v.has('pause'):
                        paused=True;break
                    if v.has('menu') or v.danger():break
                    self.w.tap(0x01)
                    self.w.wait(.9)
                    if self.frame().has('pause'):
                        paused=True;break
                status+='; '+('pause verified' if paused else 'pause not verified')
        except Halt as exc:
            status='Manual/safety stop after Juiced: '+str(exc)
        except Exception as exc:
            status='Could not finish payout/pause: '+str(exc)
            logging.exception('Juiced finish error; never resuming farm')
        finally:
            if folder is not None and report is not None:
                try:
                    evidence.save_result(folder,self.last_img,'final_screen')
                    if evidence.peak_jpeg:
                        write_bytes(folder/'highest_observed_balance.jpg',evidence.peak_jpeg)
                    if evidence.gain_jpeg:
                        write_bytes(folder/'largest_observed_balance_increase.jpg',evidence.gain_jpeg)
                    report.update(pause_verified=paused,payout_completion_verified=completed,
                                  final_status=status,highest_observed_balance=evidence.peak,
                                  largest_observed_balance_increase=evidence.peak_gain)
                    write_bytes(folder/'report.json',json.dumps(report,indent=2).encode('utf-8'))
                except Exception:logging.exception('Could not finalize Juiced report')
            log('JUICED: %s | stopped permanently until manual action',status)
            try:
                for freq in (1000,1300,1600):
                    winsound.Beep(freq,180)
            except Exception:pass
        raise Halt('Juiced unlocked; '+status+'; no reload performed')

    def run_farm(self,full):
        route=None;respawn=None;self.full_mode=full
        if full:
            route=read_recorded_route(ROUTE)
            if not route:log('No F7 route recorded; will load save and try direct desktop/desk entry')
            respawn=read_recorded_route(RESPAWN_ROUTE) or route
        self.save_route=route;self.respawn_route=respawn
        v=self.frame()
        if v.overwrite_yes() is not None or v.has('difficulty'):
            self.stop('SAVE100 will not start a new game or overwrite; return to main menu')
        if full and v.has('menu'):started=self.load_with_recovery(route)
        elif v.on_desktop() and not v.danger():started=time.monotonic()
        elif full and v.danger()=='death':
            if self.resume_after_death(respawn):started=time.monotonic()
            else:self.return_menu();started=self.load_with_recovery(route)
        elif full and (v.danger() in HACKS or v.desk_prompt() or (route and anchor_score(v,route['anchor'])>=.90)):
            started=self.load_with_recovery(route,from_spawn=True)
        elif full and (v.danger() or dark_respawn(v) or dark_frame(v)):
            self.return_menu();started=self.load_with_recovery(route)
        elif full and not v.has('pause'):started=self.load_with_recovery(route,from_spawn=True)
        else:self.stop('F8 requires desktop; F9 requires menu or a loaded save')
        while True:
            reason=self.farm(started);log('End of session: %s',reason);self.snapshot(reason)
            if not full:raise Halt('Farm-only mode stopped: '+reason+' (F9 reloads save)')
            if reason in ('death','dark respawn') and self.resume_after_death(respawn):
                started=time.monotonic();continue
            self.return_menu();self.restarts+=1;started=self.load_with_recovery(route)

def main():
    log('WTTG3 SAVE100 v0.2.3-S7 RESPAWN RECOVERY ready. F5 diagnose | F7 record save spawn | F4 record respawn | F6 save | F8 farm only | F9 full | F10 stop | F12 exit')
    log('Saved spin total at startup: %s (live count is SpinsTotal)',SpinStats().total)
    rec=None;last={};last_focus=None
    try:
        while True:
            for vk in [0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7b]:
                down=pressed(vk);old=last.get(vk,False);last[vk]=down
                if not down or old:continue
                try:
                    if vk==0x7b:return
                    if vk==0x79:
                        if rec:
                            rec.stop();rec=None;log('Recording canceled');signal('error')
                        continue
                    if vk==0x75:
                        if not rec:raise Halt('No active recording. Use F7 at saved spawn or F4 at respawn.')
                        b=Bot(Window(rec.target));v=b.frame()
                        if not v.on_desktop() or v.danger() in HACKS:
                            b.snapshot('F6 desktop not recognized; recording still active')
                            log('F6: desktop not recognized (state=%s); recording continues. Skip hack / enter monitor and press F6 again.',v.state());signal('error');continue
                        ev=rec.stop();err=rec.error;hw=rec.target;kind=rec.route_kind;anchor=rec.anchor;rec=None
                        if err:raise Halt('Recording error: '+err)
                        if len(ev)<2 or not any(e[1] in ('k','b') for e in ev):
                            raise Halt('Too few input events; if save opens at desktop, F7 is unnecessary')
                        target=RESPAWN_ROUTE if kind=='respawn' else ROUTE
                        data={'events':ev}
                        data['anchor']=anchor
                        tmp=target.with_suffix('.tmp')
                        tmp.write_text(json.dumps(data,separators=(',',':')),encoding='utf-8')
                        tmp.replace(target)
                        log('%s saved: %s events, %.1f s',target.name,len(ev),ev[-1][0]);signal('saved')
                        continue
                    if rec:
                        if vk in (0x76,0x73):log('Recording already active. Press F6 on game desktop.');signal('start')
                        continue
                    hw=foreground();
                    if 'welcome to the game' not in title(hw).lower():raise Halt('Switch to the WTTG3 game window first')
                    w=Window(hw);b=Bot(w);v=b.frame();state=v.state();log('Window: %s | state=%s | balances=%s | clock=%s',title(hw),state,v.balances(),v.clock())
                    if vk==0x74:
                        log('Continue score: %.3f | desktop=%s | desk prompt=%s | stake=%s | SpinsTotal=%s',v.match('continue_save')[0],v.on_desktop(),v.desk_prompt(),b.stake.inspect(v),b.spin_stats.total)
                        for rp in (ROUTE,RESPAWN_ROUTE):
                            saved=read_recorded_route(rp)
                            log('Route diagnostic | %s | anchor=%s | death_template=%.3f',rp.name,('%.3f'%anchor_score(v,saved['anchor'])) if saved else 'missing',v.match('death')[0])
                        aw=AchievementWatch(ROOT);st=aw.poll(force=True)
                        log('Juiced status: %s', 'UNLOCKED' if st is True else 'LOCKED' if st is False else 'UNKNOWN (cache unavailable)')
                        b.snapshot('diagnostic');continue
                    if vk==0x76:
                        # Unknown is expected for most 3D views, especially after
                        # the temporary flashlight tutorial hint disappears.
                        # The user manually confirms the initial spawn with F7.
                        if v.on_desktop():
                            log('Already on game desktop; no F7 route needed. Press F9.');signal('saved');continue
                        if state not in ('spawn','unknown','death','enter_desk','sit_desk') or v.danger() in ('power','offline','hack_skull','hack_terminal') or dark_frame(v) or v.on_desktop():
                            raise Halt('F7 requires the saved 3D spawn after the hack, not '+state)
                        rec=Recorder(hw);rec.route_kind='initial';rec.anchor=encode_anchor(v)
                        log('Save-spawn recording started. Walk to desk and enter computer; press F6 when the desktop appears.');signal('start')
                    if vk==0x73:
                        # The flashlight hint may make state() report spawn on
                        # the death screen. Check the actual death-room match.
                        if v.danger()!='death' or v.has('menu') or v.has('pause') or v.has('desktop'):
                            raise Halt('F4 requires the verified respawn room (skeleton wall). Do not move before recording.')
                        anchor=encode_anchor(v)
                        rec=Recorder(hw);rec.route_kind='respawn';rec.anchor=anchor
                        log('Respawn recording started. Turn, walk to desk, enter computer; press F6 on desktop.');signal('start')
                    if vk in (0x77,0x78):b.run(vk==0x78)
                except Halt as e:
                    log('STOP: %s',e)
                    if vk in (0x75,0x76,0x73):signal('error')
                except Exception as e:
                    logging.exception('Error: %s',e)
                    if vk in (0x75,0x76,0x73):signal('error')
            if rec and (foreground()!=rec.target or rec.error):
                why=rec.error or 'game lost focus'
                rec.stop();rec=None;log('Recording canceled: %s',why);signal('error')
            time.sleep(.04)
    finally:
        if rec:rec.stop()
if __name__=='__main__':main()
