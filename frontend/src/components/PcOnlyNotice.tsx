import { Monitor } from "lucide-react";

/** 手机端专用：电脑独占功能（模型下载 / 节点管理 / LoRA 数据等）的收口说明。
 *  这些页面要读写电脑上的 ComfyUI 目录、直连图站与本地网关，手机上既没有那些目录
 *  也会被后端远端闸门挡下（403 / 连接被拒）⇒ 不再渲染表单，只讲清「去哪做、怎么带回来」。 */
export function PcOnlyNotice(props: {
  title: string;
  reason: string;
  stillWorks?: string;
  howTo?: string;
}) {
  return (
    <div className="pc-only-notice">
      <Monitor size={26} aria-hidden="true" />
      <h2>{props.title}</h2>
      <p className="pc-only-tag">此功能需在电脑端操作</p>
      <p className="pc-only-reason">{props.reason}</p>
      {props.stillWorks && <p className="pc-only-keep">{props.stillWorks}</p>}
      {props.howTo && <p className="pc-only-how">{props.howTo}</p>}
    </div>
  );
}
