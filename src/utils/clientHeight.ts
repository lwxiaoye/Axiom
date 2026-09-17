// 获取页面高度
export const clientHeight = (res?: number, min = 200) => {
  let height = document.documentElement.clientHeight - (res ? res : 200);
  if (min && height < min) {
    height = min;
  }
  return `height:${height}px`;
};

export const getHeightNumber = (res: number, min = 200, max?: number) => {
  if (res != null) {
    let height = document.documentElement.clientHeight - (res ? res : 200);
    if (min && height < min) {
      height = min;
    }
    if (max && height > max) {
      height = max;
    }
    return `${height}px`;
  }
};
export const clientWidth = (res: number) => {
  if (res != null) {
    return `width:${document.documentElement.clientWidth - res}px`;
  } else {
    return `width:${document.documentElement.clientWidth - 200}px`;
  }
};
