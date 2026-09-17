import { commentaryRepeatsFinalAnswer } from './commentaryVisibility';

describe('commentary visibility boundary', () => {
  const draft = `
入学报到怎么办理

报到时间与地点：新生在规定时间到录取专业对应校区报到，需要携带录取通知书和身份证。
现场先确认是否完成网上缴费。已缴费的同学先领取宿舍钥匙，再到学院报到点办理手续；未缴费的同学先到学院报到点，再缴费、安排床位并办理入住。
各学院报到点按校区划分，绿色通道与现金缴费也分别设在两个校区的综合服务大厅。

校园风光

学校官网的校园风景栏目展示教学楼、学生活动中心、图书馆、实训公园、足球场和校园夜景。想提前了解校园，可以从官网栏目查看，也可以在报到当天沿主路参观标志性建筑。
以上信息均需以学校当年发布的最新通知为准。
`;

  const finalAnswer = `
入学报到怎么办理

报到时间与地点：新生在规定时间到录取专业对应校区报到，必须带齐录取通知书和身份证。
现场先确认是否完成网上缴费。已缴费的同学先领取宿舍钥匙，再到学院报到点办理手续；未缴费的同学先到学院报到点，再缴费、安排床位并办理入住。
各学院报到点按校区划分，绿色通道与现金缴费也分别设在两个校区的综合服务大厅。

校园风光

学校官网的校园风景栏目展示了教学楼、学生活动中心、图书馆、实训公园、足球场和校园夜景。想提前了解校园，可以从官网栏目查看，也可以在报到当天沿主路参观标志性建筑。
以上信息均需以学校当年发布的最新通知为准。
`;

  it('hides a persisted answer-sized commentary draft that repeats the final answer', () => {
    expect(commentaryRepeatsFinalAnswer(draft, finalAnswer)).toBe(true);
  });

  it('keeps short progress commentary even when it shares the final topic', () => {
    expect(
      commentaryRepeatsFinalAnswer('入学材料已经从知识库核对清楚，接下来查看学校官网的校园照片。', finalAnswer),
    ).toBe(false);
  });

  it('keeps a long commentary block that does not repeat the final answer', () => {
    const unrelated = `${'已核对版式、字体和页面留白，下一步验证导出文件。'.repeat(12)}`;
    expect(commentaryRepeatsFinalAnswer(unrelated, finalAnswer)).toBe(false);
  });
});
