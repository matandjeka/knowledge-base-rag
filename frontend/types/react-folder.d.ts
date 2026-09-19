import "react";
declare module "react" {
  interface InputHTMLAttributes<T> {
    /** Non-standard but widely supported: lets a file input pick a whole folder. */
    webkitdirectory?: string;
  }
}
