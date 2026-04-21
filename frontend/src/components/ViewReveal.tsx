/**
 * ViewReveal — Scroll-triggered entrance animation wrapper.
 *
 * Replicates the core scroll-animation pattern from yonatanhershko.vercel.app:
 *   gsap.fromTo(el, {x: -100, opacity: 0, scale: .4}, {x: 0, scale: 1, opacity: 1, ...})
 *   scrollTrigger: { start: "top 60%", once: true }
 *
 * Usage:
 *   <ViewReveal>          — default slide-up
 *   <ViewReveal from="left">   — slide from left
 *   <ViewReveal from="right">  — slide from right
 *   <ViewReveal from="scale">  — scale + fade
 *   <ViewReveal delay={0.15}>  — stagger manually
 */
import React from 'react';
import { m, Variants } from 'framer-motion';

type Direction = 'up' | 'down' | 'left' | 'right' | 'scale' | 'none';

interface ViewRevealProps {
  children: React.ReactNode;
  from?: Direction;
  delay?: number;
  duration?: number;
  distance?: number;
  className?: string;
  style?: React.CSSProperties;
  id?: string;
  as?: keyof JSX.IntrinsicElements;
}

const buildVariants = (from: Direction, distance: number): Variants => {
  const hidden: Record<string, number> = { opacity: 0 };
  const visible: Record<string, number> = { opacity: 1 };

  switch (from) {
    case 'left':
      hidden.x = -distance;
      hidden.scale = 0.94;
      visible.x = 0;
      visible.scale = 1;
      break;
    case 'right':
      hidden.x = distance;
      hidden.scale = 0.94;
      visible.x = 0;
      visible.scale = 1;
      break;
    case 'down':
      hidden.y = -distance * 0.5;
      visible.y = 0;
      break;
    case 'scale':
      hidden.scale = 0.82;
      hidden.y = 16;
      visible.scale = 1;
      visible.y = 0;
      break;
    case 'none':
      break;
    case 'up':
    default:
      hidden.y = distance;
      visible.y = 0;
      break;
  }

  return { hidden, visible };
};

const ViewReveal: React.FC<ViewRevealProps> = ({
  children,
  from = 'up',
  delay = 0,
  duration = 0.65,
  distance = 32,
  className,
  style,
  id,
}) => {
  const variants = buildVariants(from, distance);

  return (
    <m.div
      id={id}
      className={className}
      style={style}
      variants={variants}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: '-8% 0px' }}
      transition={{
        type: 'spring',
        damping: 26,
        stiffness: 88,
        delay,
        opacity: { duration, ease: [0.22, 1, 0.36, 1] },
      }}
    >
      {children}
    </m.div>
  );
};

export default ViewReveal;

/**
 * StaggerReveal — wraps a list of children and staggers their entry
 * using Framer Motion variants, matching Yonatan's `stagger: .1` pattern.
 *
 * Usage:
 *   <StaggerReveal>
 *     {items.map(i => <StaggerItem key={i.id}>{...}</StaggerItem>)}
 *   </StaggerReveal>
 */
const staggerContainer: Variants = {
  hidden: {},
  visible: {
    transition: {
      staggerChildren: 0.08,
      delayChildren: 0.05,
    },
  },
};

const staggerItem: Variants = {
  hidden: { opacity: 0, y: 22, scale: 0.96 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      type: 'spring',
      damping: 26,
      stiffness: 90,
    },
  },
};

interface StaggerRevealProps {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export const StaggerReveal: React.FC<StaggerRevealProps> = ({ children, className, style }) => (
  <m.div
    className={className}
    style={style}
    variants={staggerContainer}
    initial="hidden"
    whileInView="visible"
    viewport={{ once: true, margin: '-6% 0px' }}
  >
    {children}
  </m.div>
);

export const StaggerItem: React.FC<{ children: React.ReactNode; className?: string; style?: React.CSSProperties }> = ({
  children,
  className,
  style,
}) => (
  <m.div className={className} style={style} variants={staggerItem}>
    {children}
  </m.div>
);
