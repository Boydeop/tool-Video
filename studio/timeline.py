"""Frame-aligned audio-first quiz timeline.

Order per question:
  (1) show and speak the short clue
  (2) finish speaking, then start the untouched full countdown
  (3) reveal the correct answer, reading the Korean pronunciation
  (4) transition to the next question

A speech clip's measured duration, not a fixed assumption, determines the
countdown start. This keeps slow TTS voices from overlapping the timer.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Scene:
    start: float
    prompt_duration: float
    countdown_start: float
    answer_start: float
    answer_duration: float
    transition_start: float
    end: float
    think: float

    def local(self,absolute_time):
        return float(absolute_time)-self.start

    @property
    def duration(self):
        return self.end-self.start

def _snap(seconds,fps):
    # Whole frames guarantee video, narration, tick and reveal boundaries align.
    return int(math.ceil(max(0,float(seconds))*fps-1e-8))/fps

def build_scenes(count,think,reveal,transition,intro=1.5,fps=24,
                 prompt_lengths=None,answer_lengths=None,voice_lead=.18,
                 default_prompt=1.35):
    if count<1 or not 4<=think<=12 or not 1.5<=reveal<=7 or not .3<=transition<=2:
        raise ValueError("Invalid quiz timing")
    if fps not in (24,30):
        raise ValueError("Unsupported frame rate")
    prompt_lengths=prompt_lengths or [0]*count
    answer_lengths=answer_lengths or [0]*count
    if len(prompt_lengths)!=count or len(answer_lengths)!=count:
        raise ValueError("TTS duration list does not match questions")
    scenes=[]
    current=_snap(intro,fps)
    for i in range(count):
        clip_length=max(0,float(prompt_lengths[i]))
        korean_length=max(0,float(answer_lengths[i]))
        # A little padding after the actual spoken word is intentional.
        prompt_duration=_snap(max(default_prompt,voice_lead+clip_length+.38),fps)
        answer_duration=_snap(max(reveal,voice_lead+korean_length+.38),fps)
        countdown_start=current+prompt_duration
        answer_start=countdown_start+_snap(think,fps)
        transition_start=answer_start+answer_duration
        end=transition_start+_snap(transition,fps)
        scenes.append(Scene(round(current,6),prompt_duration,round(countdown_start,6),
                           round(answer_start,6),answer_duration,
                           round(transition_start,6),round(end,6),think))
        current=end
    return scenes

def duration(scenes):
    if not scenes:raise ValueError("Empty timeline")
    return scenes[-1].end
