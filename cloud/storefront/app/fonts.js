import { DM_Serif_Display, Inter, Noto_Sans_Ethiopic, Nunito, Poppins } from 'next/font/google';

const inter = Inter({ subsets: ['latin'], variable: '--f-inter', display: 'swap' });
const poppins = Poppins({ subsets: ['latin'], weight: ['400', '500', '600', '700'], variable: '--f-poppins', display: 'swap' });
const serif = DM_Serif_Display({ subsets: ['latin'], weight: '400', variable: '--f-serif', display: 'swap' });
const nunito = Nunito({ subsets: ['latin'], variable: '--f-nunito', display: 'swap' });
const ethiopic = Noto_Sans_Ethiopic({ subsets: ['ethiopic'], weight: ['400', '600', '700'], variable: '--f-ethiopic', display: 'swap' });

export const fontVars = [inter, poppins, serif, nunito, ethiopic].map((f) => f.variable).join(' ');

// body font, display font, for each choice in the Website app
export const FONT_SETS = {
  inter: ['var(--f-inter)', 'var(--f-inter)'],
  poppins: ['var(--f-poppins)', 'var(--f-poppins)'],
  dm_serif: ['var(--f-inter)', 'var(--f-serif)'],
  nunito: ['var(--f-nunito)', 'var(--f-nunito)'],
  noto_ethiopic: ['var(--f-ethiopic)', 'var(--f-ethiopic)'],
};
