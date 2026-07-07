using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddTrackedCardKind : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId",
                table: "TrackedCards");

            migrationBuilder.AddColumn<string>(
                name: "Kind",
                table: "TrackedCards",
                type: "TEXT",
                nullable: false,
                defaultValue: "wishlist");   // existing tracked cards become Wishlist

            migrationBuilder.CreateIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId_Kind",
                table: "TrackedCards",
                columns: new[] { "UserName", "Game", "ProductId", "Kind" },
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId_Kind",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "Kind",
                table: "TrackedCards");

            migrationBuilder.CreateIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId",
                table: "TrackedCards",
                columns: new[] { "UserName", "Game", "ProductId" },
                unique: true);
        }
    }
}
