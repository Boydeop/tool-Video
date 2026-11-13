"""Original offline synthesized sound effects, no third-party audio assets."""
from array import array
import math
from pathlib import Path
import random
import wave

SAMPLE_RATE=44100

def _sfx_notes(samples,start_sec,notes,volume=1):
    for n,(hz,duration) in enumerate(notes):
        start=int((start_sec+sum(item[1] for item in notes[:n]))*SAMPLE_RATE)
        count=int(duration*SAMPLE_RATE)
        for k in range(count):
            i=start+k
            if not 0<=i<len(samples):break
            t=k/SAMPLE_RATE
            env=(1-t/max(.01,duration))**1.6
            v=math.sin(2*math.pi*hz*t)*env*10000*volume
            samples[i]=max(-32767,min(32767,samples[i]+int(v)))

def build_sfx(path,questions,think,reveal,transition,intro,gain=.34):
    """Build a single PCM track for countdown ticks, answer chime, transitions."""
    if not 0<=gain<=1:raise ValueError("Invalid SFX volume")
    total=int(round((intro+questions*(think+reveal+transition))*SAMPLE_RATE))
    samples=array("h",[0])*total
    # Intro sparkle.
    _sfx_notes(samples,.45,[(540,.10),(680,.10),(820,.20)],gain*.58)
    cycle=think+reveal+transition
    for i in range(questions):
        start=intro+i*cycle
        # Short tick each second in the second half of the countdown.
        for offset in range(1,int(think)+1):
            if offset<think-.2:
                _sfx_notes(samples,start+offset,[(970,.055)],gain*.48)
        # Positive answer chime.
        _sfx_notes(samples,start+think+.04,[(740,.13),(940,.13),(1175,.25)],gain*.82)
        if i<questions-1:
            # Quiet original soft slide swoosh with deterministic noise.
            rng=random.Random(i+71)
            begin=int((start+think+reveal+.06)*SAMPLE_RATE)
            for j in range(int(min(.45,transition)*SAMPLE_RATE)):
                p=begin+j
                if p>=len(samples):break
                position=j/(.45*SAMPLE_RATE)
                envelope=max(0,math.sin(math.pi*position))**2
                v=(rng.random()-.5)*1800*envelope*gain
                samples[p]=max(-32767,min(32767,samples[p]+int(v)))
    output=Path(path);output.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(output),"wb") as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(SAMPLE_RATE)
        file.writeframes(samples.tobytes())
    return output
