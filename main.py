"""Tiếng Hàn So Easy Studio CLI."""
import argparse
import json
from studio.service import Service
from studio.scheduler import Scheduler

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",default="data")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("serve"); sub.add_parser("worker"); sub.add_parser("jobs")
    sub.add_parser("questions")
    d=sub.add_parser("daily"); d.add_argument("--count",type=int,default=6)
    g=sub.add_parser("generate")
    g.add_argument("--topic",required=True);g.add_argument("--level",default="TOPIK 1")
    g.add_argument("--count",type=int,default=6)
    a=sub.add_parser("approve");a.add_argument("ids",nargs="+")
    r=sub.add_parser("render");r.add_argument("ids",nargs="+")
    r.add_argument("--theme",choices=["hanji","blue","sejong"],default="hanji")
    r.add_argument("--width",type=int,choices=[540,720,1080],default=540)
    r.add_argument("--voice",action="store_true",help="Online Vietnamese and Korean narration")
    s=sub.add_parser("schedule")
    s.add_argument("--video",required=True);s.add_argument("--caption",required=True)
    s.add_argument("--at",required=True)
    args=parser.parse_args();service=Service(args.data)
    if args.command=="serve":
        from studio.webapp import serve
        serve(service);return
    if args.command=="worker":
        Scheduler(service.store).run();return
    if args.command=="daily": result=service.daily(count=args.count)
    elif args.command=="generate": result=service.generate(args.topic,args.level,args.count)
    elif args.command=="approve":
        service.store.approve(args.ids);result={"approved":args.ids}
    elif args.command=="render":
        result={"video":service.render_approved(args.ids,args.theme,width=args.width,with_voice=args.voice)}
    elif args.command=="schedule":
        result={"job_id":service.schedule(args.video,args.caption,args.at)}
    elif args.command=="questions": result=service.store.list_questions()
    else: result=service.store.list_jobs()
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
