import type { ReactNode } from 'react';

/**
 * Small line-icon set matching the design system. Each icon is a plain SVG
 * that inherits color from its containing element (currentColor), sized via
 * the `size` prop so one component works at every scale the design uses
 * (24px nav tabs, 16-18px buttons, 14px compact toggle buttons).
 */
interface IconProps {
  size?: number;
}

const stroke = (size: number, children: ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    {children}
  </svg>
);

export const IconSave = ({ size = 16 }: IconProps) => stroke(size, <>
  <path d="M5 4h11l3 3v13H5z" strokeLinejoin="round" />
  <path d="M8 4v6h8V4M8 14h8v6H8z" strokeLinejoin="round" />
</>);

export const IconEye = ({ size = 16 }: IconProps) => stroke(size, <>
  <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" strokeLinejoin="round" />
  <circle cx="12" cy="12" r="3" />
</>);

export const IconRefresh = ({ size = 16 }: IconProps) => stroke(size, <>
  <path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3" strokeLinecap="round" />
  <path d="M18 3v4h-4M6 21v-4h4" strokeLinecap="round" strokeLinejoin="round" />
</>);

export const IconSpinner = ({ size = 16 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="icon-spin">
    <circle cx="12" cy="12" r="9" strokeDasharray="14 8" strokeLinecap="round" />
  </svg>
);

export const IconLogout = ({ size = 16 }: IconProps) => stroke(size, <>
  <path d="M9 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h4" strokeLinecap="round" strokeLinejoin="round" />
  <path d="M16 17l5-5-5-5M21 12H9" strokeLinecap="round" strokeLinejoin="round" />
</>);

export const IconCheck = ({ size = 16 }: IconProps) => stroke(size,
  <path d="M20 6 9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
);

export const IconArrowLeft = ({ size = 16 }: IconProps) => stroke(size,
  <path d="M19 12H5M11 6l-6 6 6 6" strokeLinecap="round" strokeLinejoin="round" />
);

export const IconStar = ({ size = 16 }: IconProps) => stroke(size,
  <path d="M12 3l2.4 5.8L21 9.3l-4.5 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3L3 9.3l6.6-.5z" strokeLinejoin="round" />
);

export const IconArrowRight = ({ size = 16 }: IconProps) => stroke(size,
  <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
);

export const IconHome = ({ size = 24 }: IconProps) => stroke(size, <>
  <path d="M4 11.5 12 4l8 7.5" strokeLinecap="round" strokeLinejoin="round" />
  <path d="M6.5 10v9h11v-9" strokeLinecap="round" strokeLinejoin="round" />
</>);

export const IconLearn = ({ size = 24 }: IconProps) => stroke(size, <>
  <path d="M4 19.5V6a2 2 0 0 1 2-2h8l6 6v9.5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" strokeLinejoin="round" />
  <path d="M14 4v5a1 1 0 0 0 1 1h5" strokeLinejoin="round" />
  <path d="M8 13h8M8 16.5h5" strokeLinecap="round" />
</>);

export const IconBullet = ({ size = 14 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24"><circle cx="12" cy="12" r="6" fill="currentColor" /></svg>
);

export const IconBlitz = ({ size = 14 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24"><polygon points="13,2 4,14 11,14 9,22 20,10 12,10" fill="currentColor" /></svg>
);

export const IconRapid = ({ size = 14 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
    <circle cx="12" cy="12" r="9" />
    <line x1="12" y1="12" x2="12" y2="7" strokeLinecap="round" />
    <line x1="12" y1="12" x2="16" y2="13" strokeLinecap="round" />
  </svg>
);

export const IconClassical = ({ size = 14 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
    <rect x="5" y="5" width="14" height="14" rx="2" />
  </svg>
);

export const IconCorrespondence = ({ size = 14 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M4 7l8 6 8-6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
