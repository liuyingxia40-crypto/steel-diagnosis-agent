import { useEffect, type RefObject } from "react"
import gsap from "gsap"
import { MotionPathPlugin } from "gsap/MotionPathPlugin"
import type { SystemId } from "./model"

gsap.registerPlugin(MotionPathPlugin)

function pace(system: SystemId, focus: SystemId | null, allow: boolean): number {
  if (!allow) return 0
  if (focus === null) return 0.58
  return focus === system ? 1 : 0.16
}

export function useProcessMotion(svgRef: RefObject<SVGSVGElement | null>, enabled: boolean, focus: SystemId | null, gain = 1) {
  useEffect(() => {
    const svg = svgRef.current
    if (!svg || !enabled || gain <= 0) return

    const tweens: gsap.core.Tween[] = []
    const ctx = gsap.context(() => {
      svg.querySelectorAll<SVGGraphicsElement>("[data-spin]").forEach((node) => {
        const system = node.dataset.system as SystemId | undefined
        if (!system) return
        const speed = Number(node.dataset.speed || "1")
        const dir = node.dataset.dir === "-1" ? -1 : 1
        const tween = gsap.to(node, {
          rotation: dir * 360,
          transformOrigin: "50% 50%",
          duration: 2.5 / Math.max(speed, 0.12),
          repeat: -1,
          ease: "none",
        })
        tween.timeScale(pace(system, focus, true) * gain)
        if (document.hidden) tween.pause()
        tweens.push(tween)
      })

      svg.querySelectorAll<SVGPathElement>("[data-flow]").forEach((path) => {
        const system = path.dataset.system as SystemId | undefined
        if (!system || path.getTotalLength() < 1) return
        const jet = path.dataset.flow === "jet"
        const rate = Number(path.dataset.rate || "0.7")
        gsap.set(path, { strokeDasharray: jet ? "3 10" : "8 13" })
        const tween = gsap.to(path, {
          strokeDashoffset: -path.getTotalLength(),
          duration: jet ? 1.7 : 2.7 / Math.max(rate, 0.2),
          repeat: -1,
          ease: "none",
        })
        tween.timeScale(pace(system, focus, true) * gain)
        if (document.hidden) tween.pause()
        tweens.push(tween)
      })

      svg.querySelectorAll<SVGCircleElement>("[data-particle]").forEach((dot) => {
        const system = dot.dataset.system as SystemId | undefined
        const path = dot.dataset.path
        if (!system || !path) return
        const visible = focus === null ? dot.dataset.priority === "main" : focus === system
        if (!visible) {
          gsap.set(dot, { autoAlpha: 0 })
          return
        }
        const tween = gsap.to(dot, {
          autoAlpha: 0.92,
          duration: 3.6 / Math.max(Number(dot.dataset.rate || "0.7"), 0.25),
          delay: Number(dot.dataset.delay || "0"),
          repeat: -1,
          ease: "none",
          motionPath: {
            path,
            align: path,
            alignOrigin: [0.5, 0.5],
          },
        })
        tween.timeScale(pace(system, focus, true) * gain)
        if (document.hidden) tween.pause()
        tweens.push(tween)
      })
    }, svg)

    const onVisibility = () => {
      tweens.forEach((tween) => (document.hidden ? tween.pause() : tween.resume()))
    }
    document.addEventListener("visibilitychange", onVisibility)
    return () => {
      document.removeEventListener("visibilitychange", onVisibility)
      ctx.revert()
    }
  }, [enabled, focus, gain, svgRef])
}
